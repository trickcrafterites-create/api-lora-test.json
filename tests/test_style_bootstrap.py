import contextlib
import hashlib
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import download_loras as downloader
import style_bootstrap as styles


class Response(io.BytesIO):
    def __init__(self, data, content_type="application/octet-stream"):
        super().__init__(data)
        self.status = 200
        self.headers = {"Content-Type": content_type, "Content-Length": str(len(data))}


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


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.output = self.directory / "loras"
        self.host = "teststore.private.blob.vercel-storage.com"
        self.environment = {"AELIX_STYLE_BOOTSTRAP": "1", "AELIX_STYLE_BROKER_BASE": styles.BROKER_BASE,
                            "AELIX_STYLE_BROKER_TOKEN": "example-scoped-secret", "AELIX_STYLE_ASSET_HOST": self.host}
        header = json.dumps({"weight": {"dtype": "F32", "shape": [1], "data_offsets": [0, 4]}}).encode()
        self.payload = struct.pack("<Q", len(header)) + header + b"\0\0\0\0"
        self.models = [{"id": model_id, "filename": f"style-{index}.safetensors",
                        "downloadUrl": "https://example.invalid/unused-source", "baseModel": "Illustrious",
                        "sha256": hashlib.sha256(self.payload).hexdigest(), "sizeBytes": len(self.payload)}
                       for index, model_id in enumerate(sorted(styles.PINNED_IDS))]
        self.manifest = self.directory / "manifest.json"
        self.manifest.write_text(json.dumps({"schemaVersion": 1, "baseModel": "Illustrious", "models": self.models}))

    def document(self, model):
        return {"url": f"https://{self.host}/{model['filename']}?vercel-blob-signature=example-url-secret",
                "expiresAt": int((time.time() + 600) * 1000), "sha256": model["sha256"], "sizeBytes": model["sizeBytes"]}

    def broker(self, document):
        return Response(json.dumps(document).encode(), "application/json")

    def test_complete_download_pins_bytes_and_scopes_authorization(self):
        responses = []
        for model in self.models:
            responses.extend([self.broker(self.document(model)), Response(self.payload)])
        opener = Opener(*responses)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            styles.bootstrap(self.manifest, self.output, self.environment, opener=opener)
        self.assertEqual(len(opener.requests), 4)
        for index, model in enumerate(self.models):
            broker_request, weight_request = opener.requests[index * 2:index * 2 + 2]
            self.assertEqual(broker_request.full_url, styles.BROKER_BASE + "/" + model["id"])
            self.assertEqual(broker_request.get_header("Authorization"), "Bearer example-scoped-secret")
            self.assertIsNone(weight_request.get_header("Authorization"))
            self.assertEqual((self.output / model["filename"]).read_bytes(), self.payload)
        self.assertNotIn("example-url-secret", output.getvalue())
        self.assertNotIn("example-scoped-secret", output.getvalue())

    def test_cached_verified_files_need_no_credential_or_network(self):
        self.output.mkdir()
        for model in self.models:
            (self.output / model["filename"]).write_bytes(self.payload)
        opener = Opener()
        with contextlib.redirect_stdout(io.StringIO()):
            styles.bootstrap(self.manifest, self.output, {}, opener=opener)
        self.assertEqual(opener.requests, [])

    def test_forged_broker_metadata_never_downloads_weights(self):
        for change in [{"sha256": "0" * 64}, {"sizeBytes": True}, {"sizeBytes": len(self.payload) + 1},
                       {"expiresAt": int(time.time() * 1000)}, {"url": "https://evil.example/model"}]:
            with self.subTest(change=change):
                opener = Opener(self.broker(dict(self.document(self.models[0]), **change)))
                with self.assertRaises(downloader.DownloadError):
                    styles.bootstrap(self.manifest, self.output, self.environment, opener=opener)
                self.assertEqual(len(opener.requests), 1)
                self.assertFalse((self.output / self.models[0]["filename"]).exists())

    def test_corrupt_weight_is_never_exposed(self):
        opener = Opener(self.broker(self.document(self.models[0])), Response(self.payload[:-4] + b"bad!"),
                        Response(self.payload[:-4] + b"bad!"))
        with patch.object(downloader.RequestStartLimiter, "pause"), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(downloader.DownloadError, "SHA256 mismatch"):
                styles.bootstrap(self.manifest, self.output, self.environment, opener=opener)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_broker_and_asset_redirects_are_rejected_without_destination_disclosure(self):
        request = urllib.request.Request(styles.BROKER_BASE)
        with self.assertRaises(downloader.DownloadError) as caught:
            styles.NoRedirects().redirect_request(request, None, 302, "Found", {}, "https://evil.example/secret")
        self.assertNotIn("evil", str(caught.exception))

    def test_exact_broker_and_private_store_configuration(self):
        for key, value in [("AELIX_STYLE_BROKER_BASE", "https://aelixhub.red.evil.example/api/style-tests/assets"),
                           ("AELIX_STYLE_ASSET_HOST", "other.public.blob.vercel-storage.com"),
                           ("AELIX_STYLE_ASSET_HOST", self.host + ":443"),
                           ("AELIX_STYLE_BROKER_TOKEN", "secret\nInjected: header")]:
            with self.subTest(key=key), self.assertRaises(downloader.DownloadError):
                styles.configuration(dict(self.environment, **{key: value}))

    def test_signed_link_cannot_change_store_or_insert_credentials_fragment_or_port(self):
        for url in ["http://" + self.host + "/file", "https://user:pass@" + self.host + "/file",
                    "https://" + self.host + ":443/file", "https://" + self.host + "/file#fragment",
                    "https://other.private.blob.vercel-storage.com/file"]:
            with self.subTest(url=url), self.assertRaises(downloader.DownloadError):
                styles.validate_response(dict(self.document(self.models[0]), url=url), self.models[0], self.host)

    def test_response_size_and_content_type_are_bounded(self):
        for response in [Response(b"<html>secret</html>", "text/html"),
                         Response(b" " * (styles.MAX_BROKER_BYTES + 1), "application/json")]:
            with self.assertRaises(downloader.DownloadError):
                styles.bootstrap(self.manifest, self.output, self.environment, opener=Opener(response))

    def test_missing_space_stops_before_network(self):
        opener = Opener()
        with patch.object(styles.shutil, "disk_usage", return_value=type("Disk", (), {"free": 1})()):
            with self.assertRaisesRegex(downloader.DownloadError, "free disk"):
                styles.bootstrap(self.manifest, self.output, self.environment, opener=opener)
        self.assertEqual(opener.requests, [])

    def test_default_off_preserves_start_and_scrubs_optional_credential(self):
        calls = []
        environment = {"AELIX_STYLE_BROKER_TOKEN": "example-secret"}
        with patch.object(styles, "bootstrap") as bootstrap:
            styles.main(environment, lambda *args: calls.append(args))
        bootstrap.assert_not_called()
        self.assertEqual(calls, [("/start.sh", ["/start.sh"])])
        self.assertNotIn("AELIX_STYLE_BROKER_TOKEN", environment)

    def test_failed_optional_bootstrap_preserves_start_and_never_logs_untrusted_error(self):
        calls = []
        with patch.object(styles, "bootstrap", side_effect=RuntimeError("https://secret-url?token=example-secret")):
            with contextlib.redirect_stderr(io.StringIO()) as log:
                styles.main(self.environment, lambda *args: calls.append(args))
        self.assertEqual(calls, [("/start.sh", ["/start.sh"])])
        self.assertNotIn("example-secret", log.getvalue())
        self.assertNotIn("secret-url", log.getvalue())
        self.assertNotIn("AELIX_STYLE_BROKER_TOKEN", self.environment)


if __name__ == "__main__":
    unittest.main()
