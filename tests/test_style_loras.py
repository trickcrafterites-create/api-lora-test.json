import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import struct
import tempfile
import unittest
import urllib.request
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import download_loras as downloader
import install_style_loras as styles


class StyleInstallerTests(unittest.TestCase):
    def local_fixture(self, directory):
        source = Path(directory) / "source"
        source.mkdir()
        header = json.dumps({"weight": {"dtype": "F32", "shape": [1], "data_offsets": [0, 4]}}).encode()
        payload = struct.pack("<Q", len(header)) + header + b"\0\0\0\0"
        model = {"id": "test-style", "filename": "test.safetensors",
                 "sha256": hashlib.sha256(payload).hexdigest(), "requiresAuthentication": True}
        (source / model["filename"]).write_bytes(payload)
        return source, Path(directory) / "output", model, payload

    def test_local_install_checks_exact_bytes_without_network_or_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            source, output, model, payload = self.local_fixture(directory)
            with patch.object(urllib.request, "build_opener") as network, contextlib.redirect_stdout(io.StringIO()):
                styles.install_local([model], source, output)
                network.assert_not_called()
            self.assertEqual((output / model["filename"]).read_bytes(), payload)
            self.assertEqual(len(list(output.iterdir())), 1)

    def test_invalid_local_file_leaves_previous_output_intact(self):
        with tempfile.TemporaryDirectory() as directory:
            source, output, model, payload = self.local_fixture(directory)
            output.mkdir()
            destination = output / model["filename"]
            destination.write_bytes(b"previous")
            (source / model["filename"]).write_bytes(payload[:-4] + b"bad!")
            with self.assertRaisesRegex(downloader.IntegrityError, "SHA256 mismatch"):
                styles.install_local([model], source, output)
            self.assertEqual(destination.read_bytes(), b"previous")

    def test_missing_local_selection_fails_before_copying_any_weight(self):
        with tempfile.TemporaryDirectory() as directory:
            source, output, model, _ = self.local_fixture(directory)
            missing = dict(model, id="missing", filename="missing.safetensors")
            with self.assertRaisesRegex(downloader.IntegrityError, "missing"):
                styles.install_local([model, missing], source, output)
            self.assertFalse(output.exists())

    def test_pinned_styles_are_distinct_from_existing_worker_catalog(self):
        models = downloader.load_manifest(ROOT / "catalog" / "style-loras.json")
        self.assertEqual({model["modelVersionId"] for model in models}, {2553688, 919342})
        existing = downloader.load_manifest(ROOT / "catalog" / "character-loras.json", min_models=103)
        existing_names = {model["filename"].lower() for model in existing}
        self.assertTrue(all(model["filename"].lower() not in existing_names for model in models))
        self.assertTrue(all(model["requiresAuthentication"] for model in models))
        self.assertTrue(all(not model["verification"]["inferenceTested"] for model in models))

    def test_token_is_only_attached_to_civitai_https_downloads(self):
        handler = styles.CivitaiAuthorization("example-secret")
        accepted = urllib.request.Request("https://civitai.com/api/download/models/2553688?fileId=2442115")
        handler.https_request(accepted)
        self.assertEqual(accepted.get_header("Authorization"), "Bearer example-secret")
        self.assertNotIn("Authorization", accepted.headers)
        for url in ["http://civitai.com/api/download/models/1", "https://evil.example/api/download/models/1",
                    "https://civitai.com.evil.example/api/download/models/1", "https://civitai.com:444/api/download/models/1",
                    "https://civitai.com/login", "https://cdn.example/weights?signature=example"]:
            request = urllib.request.Request(url)
            handler.https_request(request)
            self.assertIsNone(request.get_header("Authorization"), url)

    def test_secret_is_removed_on_cdn_redirect(self):
        request = urllib.request.Request("https://civitai.com/api/download/models/2553688")
        auth = styles.CivitaiAuthorization("example-secret")
        auth.https_request(request)
        redirect = downloader.HTTPSRedirectHandler().redirect_request(
            request, None, 302, "Found", {}, "https://cdn.example/model.safetensors?signature=example")
        auth.https_request(redirect)
        self.assertIsNone(redirect.get_header("Authorization"))
        self.assertNotIn("example-secret", redirect.full_url)

    def test_missing_auth_stops_before_network_or_output_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "output"
            with patch.object(downloader, "download_one") as download:
                with self.assertRaisesRegex(downloader.DownloadError, "mounted token"):
                    styles.install([{"requiresAuthentication": True}], destination, None)
                download.assert_not_called()
                self.assertFalse(destination.exists())

    def test_token_file_rejects_header_injection_and_never_echoes_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "token"
            for value in ["", "example-secret\r\nX-Header: injected", "x" * 4097, "nonascii-\u2603"]:
                path.write_text(value, encoding="utf-8")
                with self.assertRaises(downloader.DownloadError) as caught:
                    styles.read_token(path)
                self.assertNotIn("example-secret", str(caught.exception))
                self.assertNotIn(str(path), str(caught.exception))
            path.write_text("example-secret\n", encoding="utf-8")
            self.assertEqual(styles.read_token(path), "example-secret")

    def test_validation_needs_no_secret_and_does_not_generate_or_download(self):
        with patch.object(styles, "install") as install, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(styles.main(["--manifest", str(ROOT / "catalog" / "style-loras.json"), "--validate-only"]), 0)
            install.assert_not_called()

    def test_style_install_reuses_existing_verification_and_stops_on_failure(self):
        models = downloader.load_manifest(ROOT / "catalog" / "style-loras.json")
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(downloader, "download_one", side_effect=downloader.IntegrityError("SHA256 mismatch")) as download:
                with self.assertRaisesRegex(downloader.IntegrityError, "SHA256 mismatch"):
                    styles.install(models, Path(directory), "example-secret")
                self.assertEqual(download.call_count, 1)
                self.assertIs(download.call_args.args[0], models[0])
                self.assertIsInstance(download.call_args.kwargs["limiter"], downloader.RequestStartLimiter)


if __name__ == "__main__":
    unittest.main()
