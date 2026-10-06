import concurrent.futures
import hashlib
import io
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import types
import unittest
import urllib.error
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "custom_nodes"))

import aelix_private_lora
from aelix_private_lora import NODE_CLASS_MAPPINGS
from aelix_private_lora import runtime


def safetensors_bytes(data=b"\x01\x02\x03\x04", offsets=None):
    offsets = offsets or [0, len(data)]
    header = json.dumps(
        {"weight": {"dtype": "F32", "shape": [1], "data_offsets": offsets}},
        separators=(",", ":"),
    ).encode("utf-8")
    return struct.pack("<Q", len(header)) + header + data


class Response(io.BytesIO):
    def __init__(self, body, *, status=200, headers=None):
        super().__init__(body)
        self.status = status
        self.headers = headers if headers is not None else {
            "Content-Length": str(len(body)),
            "Content-Type": "application/octet-stream",
        }


class Opener:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        if not self.responses:
            raise AssertionError("unexpected network request")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class RuntimeLoraTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cache = Path(self.temp.name) / "cache"
        self.payload = safetensors_bytes()
        self.sha256 = hashlib.sha256(self.payload).hexdigest()
        self.upload_id = "11111111-1111-4111-8111-111111111111"
        self.host = "store123.private.blob.vercel-storage.com"
        host_environment = patch.dict(
            os.environ,
            {"AELIX_PRIVATE_LORA_BLOB_HOST": self.host},
        )
        host_environment.start()
        self.addCleanup(host_environment.stop)
        self.url = (
            f"https://{self.host}/style-weights/anima-loras/v1/"
            f"{self.upload_id}/{self.sha256}/private-lora.safetensors"
            "?vercel-blob-delegation=delegation.token"
            "&vercel-blob-signature=signature_value"
        )

    def materialize(self, opener):
        return runtime.materialize_signed_lora(
            self.url,
            self.sha256,
            len(self.payload),
            cache_dir=self.cache,
            opener=opener,
        )

    def test_url_accepts_only_direct_signed_private_blob_objects(self):
        self.assertEqual(runtime.validate_signed_blob_url(self.url, self.sha256), self.url)
        invalid_urls = [
            self.url.replace("https://", "http://"),
            self.url.replace("store123.private", "store123.public"),
            self.url.replace("store123.private", "store123.private.evil"),
            self.url.replace("https://", "https://user:password@"),
            self.url.replace(".com/", ".com:443/"),
            self.url + "#fragment",
            self.url.replace("&vercel-blob-signature=signature_value", ""),
            self.url.replace("signature_value", ""),
            self.url + "&vercel-blob-signature=duplicate",
            self.url.replace(
                f"/style-weights/anima-loras/v1/{self.upload_id}/{self.sha256}/private-lora.safetensors",
                "/",
            ),
        ]
        for value in invalid_urls:
            with self.subTest(value=value[:80]):
                with self.assertRaises(runtime.PrivateLoraError):
                    runtime.validate_signed_blob_url(value, self.sha256)

    def test_url_rejects_another_valid_private_blob_store(self):
        wrong_store = self.url.replace(self.host, "store999.private.blob.vercel-storage.com")
        with self.assertRaisesRegex(runtime.PrivateLoraError, "approved private"):
            runtime.validate_signed_blob_url(wrong_store, self.sha256)

    def test_url_requires_the_exact_private_lora_namespace_and_safe_path(self):
        invalid_paths = [
            self.url.replace("/style-weights/anima-loras/v1/", "/other/anima-loras/v1/"),
            self.url.replace(self.upload_id, "not-a-uuid"),
            self.url.replace("private-lora.safetensors", "../private-lora.safetensors"),
            self.url.replace("private-lora.safetensors", "private%2Flora.safetensors"),
            self.url.replace("private-lora.safetensors", "private-lora.ckpt"),
        ]
        for value in invalid_paths:
            with self.subTest(value=value[:120]):
                with self.assertRaises(runtime.PrivateLoraError):
                    runtime.validate_signed_blob_url(value, self.sha256)

    def test_url_path_digest_must_equal_the_sha256_input(self):
        wrong_digest = "f" * 64 if self.sha256 != "f" * 64 else "e" * 64
        mismatched_url = self.url.replace(f"/{self.sha256}/", f"/{wrong_digest}/")
        with self.assertRaisesRegex(runtime.PrivateLoraError, "path digest"):
            runtime.validate_signed_blob_url(mismatched_url, self.sha256)

    def test_pin_requires_exact_hash_and_at_most_four_gib(self):
        self.assertEqual(runtime.validate_pin(self.sha256.upper(), len(self.payload)), (self.sha256, len(self.payload)))
        for sha256, size in [
            ("f" * 63, 1),
            ("z" * 64, 1),
            ("f" * 64, True),
            ("f" * 64, 0),
            ("f" * 64, runtime.MAX_BLOB_BYTES + 1),
        ]:
            with self.subTest(sha256=sha256[:8], size=size):
                with self.assertRaises(runtime.PrivateLoraError):
                    runtime.validate_pin(sha256, size)

    def test_download_is_verified_and_atomically_cached_by_hash(self):
        opener = Opener(Response(self.payload))
        result = self.materialize(opener)
        self.assertEqual(result, self.cache / f"{self.sha256}.safetensors")
        self.assertEqual(result.read_bytes(), self.payload)
        self.assertEqual(len(opener.requests), 1)
        self.assertEqual(opener.requests[0][0].get_method(), "GET")
        self.assertFalse(any(path.suffix == ".part" for path in self.cache.iterdir()))

    def test_verified_cache_reuse_does_not_request_the_network(self):
        result = self.materialize(Opener(Response(self.payload)))
        offline = Opener()
        self.assertEqual(self.materialize(offline), result)
        self.assertEqual(offline.requests, [])

    def test_process_lock_collapses_concurrent_downloads_to_one_request(self):
        opener = Opener(Response(self.payload))
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: self.materialize(opener), range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(len(opener.requests), 1)

    def test_corrupt_owned_cache_entry_is_replaced(self):
        self.cache.mkdir()
        destination = self.cache / f"{self.sha256}.safetensors"
        destination.write_bytes(b"x" * len(self.payload))
        result = self.materialize(Opener(Response(self.payload)))
        self.assertEqual(result.read_bytes(), self.payload)

    def test_bad_size_hash_content_type_and_encoding_never_install(self):
        cases = [
            Response(self.payload, headers={"Content-Length": str(len(self.payload) + 1)}),
            Response(self.payload[:-1], headers={}),
            Response(self.payload[:-1] + b"x"),
            Response(self.payload, headers={"Content-Type": "text/html"}),
            Response(self.payload, headers={"Content-Encoding": "gzip"}),
        ]
        for response in cases:
            with self.subTest(headers=response.headers):
                isolated_cache = Path(self.temp.name) / f"case-{len(list(Path(self.temp.name).iterdir()))}"
                with self.assertRaises(runtime.IntegrityError):
                    runtime.materialize_signed_lora(
                        self.url,
                        self.sha256,
                        len(self.payload),
                        cache_dir=isolated_cache,
                        opener=Opener(response),
                    )
                self.assertFalse((isolated_cache / f"{self.sha256}.safetensors").exists())
                self.assertFalse(any(path.suffix == ".part" for path in isolated_cache.iterdir()))

    def test_malformed_safetensors_offsets_are_rejected(self):
        malformed = safetensors_bytes(offsets=[0, 99])
        path = Path(self.temp.name) / "malformed.safetensors"
        path.write_bytes(malformed)
        with self.assertRaisesRegex(runtime.IntegrityError, "offsets"):
            runtime.verify_safetensors_file(path, len(malformed))

    def test_redirect_handler_rejects_every_redirect(self):
        with self.assertRaisesRegex(runtime.PrivateLoraError, "redirects"):
            runtime.NoRedirectHandler().redirect_request(
                None,
                None,
                302,
                "Found",
                {},
                "https://store123.private.blob.vercel-storage.com/other",
            )

    def test_transport_errors_do_not_leak_the_signed_url(self):
        leaked = urllib.error.URLError(f"failure for {self.url}")
        with self.assertRaises(runtime.PrivateLoraError) as raised:
            self.materialize(Opener(leaked))
        self.assertNotIn("vercel-blob-delegation", str(raised.exception))
        self.assertNotIn("signature_value", str(raised.exception))

    def test_cache_selection_requires_an_actual_writable_mount(self):
        root = Path(self.temp.name)
        mount = root / "runpod-volume"
        mount.mkdir()
        persistent = mount / "aelix-anima" / "loras"
        fallback = root / "fallback"
        selected = runtime.select_cache_directory(
            persistent_mount=mount,
            persistent_cache=persistent,
            fallback_cache=fallback,
            mount_checker=lambda path: True,
        )
        self.assertEqual(selected, persistent)

        not_a_mount = root / "baked-runpod-volume"
        not_a_mount.mkdir()
        other_fallback = root / "other-fallback"
        selected = runtime.select_cache_directory(
            persistent_mount=not_a_mount,
            persistent_cache=not_a_mount / "aelix-anima" / "loras",
            fallback_cache=other_fallback,
            mount_checker=lambda path: False,
        )
        self.assertEqual(selected, other_fallback)

    def test_node_exposes_no_path_or_filename_input(self):
        node = NODE_CLASS_MAPPINGS["AelixPrivateLoraLoader"]
        inputs = set(node.INPUT_TYPES()["required"])
        self.assertEqual(
            inputs,
            {"model", "clip", "signed_url", "sha256", "size_bytes", "strength_model", "strength_clip"},
        )

    def test_node_uses_comfy_safe_loader_and_standard_lora_api(self):
        calls = []
        fake_comfy = types.ModuleType("comfy")
        fake_comfy.__path__ = []
        fake_sd = types.ModuleType("comfy.sd")
        fake_utils = types.ModuleType("comfy.utils")
        fake_utils.load_torch_file = lambda path, safe_load: calls.append(
            ("load", path, safe_load)
        ) or {"weights": True}
        fake_sd.load_lora_for_models = lambda model, clip, lora, sm, sc: calls.append(
            ("apply", model, clip, lora, sm, sc)
        ) or ("patched-model", "patched-clip")
        fake_comfy.sd = fake_sd
        fake_comfy.utils = fake_utils
        cached_path = self.cache / f"{self.sha256}.safetensors"
        with patch.object(aelix_private_lora, "materialize_signed_lora", return_value=cached_path), patch.dict(
            sys.modules,
            {"comfy": fake_comfy, "comfy.sd": fake_sd, "comfy.utils": fake_utils},
        ):
            result = NODE_CLASS_MAPPINGS["AelixPrivateLoraLoader"]().load_lora(
                "model",
                "clip",
                self.url,
                self.sha256,
                len(self.payload),
                0.8,
                0.6,
            )
        self.assertEqual(result, ("patched-model", "patched-clip"))
        self.assertEqual(calls[0], ("load", str(cached_path), True))
        self.assertEqual(calls[1], ("apply", "model", "clip", {"weights": True}, 0.8, 0.6))

    def test_anima_image_copies_only_the_node_code(self):
        dockerfile = (ROOT / "Dockerfile.anima").read_text(encoding="utf-8")
        self.assertIn(
            "COPY custom_nodes/aelix_private_lora /comfyui/custom_nodes/aelix_private_lora",
            dockerfile,
        )


if __name__ == "__main__":
    unittest.main()
