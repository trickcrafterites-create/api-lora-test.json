import contextlib
import hashlib
import http.client
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import download_loras as downloader


def tensor_bytes(data=b"\0\0\0\0"):
    header = json.dumps({"weight": {"dtype": "F32", "shape": [1], "data_offsets": [0, len(data)]}}).encode()
    return struct.pack("<Q", len(header)) + header + data


class Response(io.BytesIO):
    def __init__(self, data, status=200, headers=None):
        super().__init__(data)
        self.status = status
        self.headers = headers if headers is not None else {"Content-Length": str(len(data)), "Content-Type": "application/octet-stream"}


class Opener:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


class DownloaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.payload = tensor_bytes()
        self.model = {
            "id": "test-character", "filename": "test-character.safetensors",
            "downloadUrl": "https://civitai.com/api/download/models/123",
            "sha256": hashlib.sha256(self.payload).hexdigest(), "sizeBytes": len(self.payload),
        }
        self.destination = self.directory / self.model["filename"]
        self.partial = self.directory / f"{self.model['filename']}.{self.model['sha256'][:12]}.part"

    def download(self, opener, retries=2):
        with contextlib.redirect_stdout(io.StringIO()):
            return downloader.download_one(self.model, self.directory, retries=retries, retry_delay=0, opener=opener)

    def manifest(self, models=None):
        path = self.directory / "manifest.json"
        path.write_text(json.dumps({"schemaVersion": 1, "baseModel": "Illustrious", "models": models or [self.model]}))
        return path

    def test_manifest_accepts_pinned_public_model(self):
        self.assertEqual(downloader.load_manifest(self.manifest())[0], self.model)

    def test_manifest_rejects_unsafe_paths_secrets_and_missing_hash(self):
        for key, bad in [("filename", "../escape.safetensors"), ("filename", "escape\\evil.safetensors"),
                         ("downloadUrl", "https://example.com/?token=secret"),
                         ("downloadUrl", "https://user:secret@example.com/model"),
                         ("downloadUrl", "http://example.com/model"), ("sha256", "123"),
                         ("sizeBytes", True), ("baseModel", "Pony")]:
            with self.subTest(key=key, bad=bad):
                model = dict(self.model, **{key: bad})
                with self.assertRaises(downloader.DownloadError):
                    downloader.load_manifest(self.manifest([model]))

    def test_manifest_minimum_and_duplicates(self):
        with self.assertRaises(downloader.DownloadError):
            downloader.load_manifest(self.manifest(), min_models=100)
        for duplicate in [dict(self.model), dict(self.model, id="other", filename=self.model["filename"].upper().replace(".SAFETENSORS", ".safetensors"))]:
            with self.assertRaises(downloader.DownloadError):
                downloader.load_manifest(self.manifest([self.model, duplicate]))

    def test_download_verifies_and_atomically_installs(self):
        self.assertEqual(self.download(Opener(Response(self.payload))), "downloaded")
        self.assertEqual(self.destination.read_bytes(), self.payload)
        self.assertFalse(self.partial.exists())

    def test_existing_good_file_skips_network(self):
        self.destination.write_bytes(self.payload)
        opener = Opener()
        self.assertEqual(self.download(opener), "cached")
        self.assertEqual(opener.requests, [])

    def test_resume_uses_correct_range(self):
        offset = 40
        self.partial.write_bytes(self.payload[:offset])
        opener = Opener(Response(self.payload[offset:], 206, {"Content-Range": f"bytes {offset}-{len(self.payload)-1}/{len(self.payload)}"}))
        self.download(opener)
        self.assertEqual(opener.requests[0].get_header("Range"), f"bytes={offset}-")
        self.assertEqual(self.destination.read_bytes(), self.payload)

    def test_server_ignoring_range_restarts_file(self):
        self.partial.write_bytes(self.payload[:40])
        self.download(Opener(Response(self.payload)))
        self.assertEqual(self.destination.read_bytes(), self.payload)

    def test_completed_partial_installs_without_network(self):
        self.partial.write_bytes(self.payload)
        self.assertEqual(self.download(Opener()), "resumed")
        self.assertEqual(self.destination.read_bytes(), self.payload)

    def test_incorrect_range_is_discarded_and_retried(self):
        self.partial.write_bytes(self.payload[:40])
        bad = Response(self.payload[40:], 206, {"Content-Range": "bytes 0-4/5"})
        opener = Opener(bad, Response(self.payload))
        self.download(opener)
        self.assertIsNone(opener.requests[1].get_header("Range"))
        self.assertEqual(self.destination.read_bytes(), self.payload)

    def test_incomplete_response_resumes_download(self):
        offset = 40
        opener = Opener(Response(self.payload[:offset], headers={"Content-Length": str(len(self.payload))}),
                        Response(self.payload[offset:], 206, {"Content-Range": f"bytes {offset}-{len(self.payload)-1}/{len(self.payload)}"}))
        self.download(opener)
        self.assertEqual(opener.requests[1].get_header("Range"), "bytes=40-")
        self.assertEqual(self.destination.read_bytes(), self.payload)

    def test_html_never_installed(self):
        with self.assertRaisesRegex(downloader.DownloadError, "error document"):
            self.download(Opener(Response(b"<html>login</html>", headers={"Content-Type": "text/html"})), retries=1)
        self.assertFalse(self.destination.exists())
        self.assertFalse(self.partial.exists())

    def test_disguised_html_never_installed(self):
        self.model.pop("sizeBytes")
        html = b"<html>download denied</html>"
        self.model["sha256"] = hashlib.sha256(html).hexdigest()
        with self.assertRaisesRegex(downloader.DownloadError, "Safetensors"):
            self.download(Opener(Response(html)), retries=1)
        self.assertFalse(self.destination.exists())

    def test_hash_failure_keeps_previous_file_and_removes_partial(self):
        previous = b"previous file"
        self.destination.write_bytes(previous)
        bad_payload = self.payload[:-4] + b"bad!"
        with self.assertRaisesRegex(downloader.DownloadError, "SHA256 mismatch"):
            self.download(Opener(Response(bad_payload)), retries=1)
        self.assertEqual(self.destination.read_bytes(), previous)
        self.assertFalse(self.partial.exists())

    def test_rate_limit_retries_but_auth_failure_does_not(self):
        limited = urllib.error.HTTPError(self.model["downloadUrl"], 429, "rate limited", {}, None)
        opener = Opener(limited, Response(self.payload))
        self.download(opener)
        self.assertEqual(len(opener.requests), 2)
        self.destination.unlink()
        denied = urllib.error.HTTPError(self.model["downloadUrl"], 403, "forbidden", {}, None)
        opener = Opener(denied)
        with self.assertRaisesRegex(downloader.DownloadError, "HTTP 403"):
            self.download(opener)
        self.assertEqual(len(opener.requests), 1)

    def test_all_download_failures_fail_install(self):
        with patch.object(downloader, "download_one", side_effect=downloader.DownloadError("file unavailable")):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaisesRegex(downloader.DownloadError, "refusing an incomplete image"):
                    downloader.install([self.model], self.directory)

    def test_retry_after_is_respected_and_capped(self):
        limited = urllib.error.HTTPError(self.model["downloadUrl"], 429, "rate limited", {"Retry-After": "120"}, None)
        with patch.object(downloader.time, "sleep") as sleep:
            self.download(Opener(limited, Response(self.payload)))
        sleep.assert_called_once_with(60)

    def test_character_catalog_meets_minimum_and_retains_kim(self):
        path = Path(__file__).resolve().parents[1] / "catalog" / "character-loras.json"
        models = downloader.load_manifest(path, min_models=100)
        self.assertIn("KimPossibleIllustrious2.0JLFO.safetensors", [model["filename"] for model in models])

    def test_checkpoint_manifest_is_valid(self):
        path = Path(__file__).resolve().parents[1] / "catalog" / "checkpoint.json"
        self.assertEqual(len(downloader.load_manifest(path)), 1)

    def test_example_workflow_has_real_lora_filename_and_complete_connections(self):
        root = Path(__file__).resolve().parents[1]
        workflow = json.loads((root / "example-request.json").read_text())["input"]["workflow"]
        self.assertEqual(workflow["19"]["class_type"], "LoraLoader")
        self.assertEqual(workflow["19"]["inputs"]["lora_name"], "KimPossibleIllustrious2.0JLFO.safetensors")
        self.assertEqual(workflow["5"]["inputs"]["clip"], ["19", 1])
        self.assertEqual(workflow["6"]["inputs"]["clip"], ["19", 1])
        self.assertEqual(workflow["12"]["inputs"]["model"], ["19", 0])
        self.assertEqual(set(workflow["9"]["inputs"]), {"width", "height", "batch_size"})
        self.assertEqual(workflow["18"]["class_type"], "SaveImage")
        for node in workflow.values():
            for value in node["inputs"].values():
                if isinstance(value, list):
                    self.assertIn(value[0], workflow)


if __name__ == "__main__":
    unittest.main()
