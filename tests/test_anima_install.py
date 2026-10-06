import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from anima_install import DownloadError, load_asset_manifest, load_lora_manifest


class AnimaManifestTests(unittest.TestCase):
    def setUp(self):
        self.assets_path = ROOT / "catalog" / "anima-assets.json"
        self.loras_path = ROOT / "catalog" / "anima-loras.json"
        self.assets = json.loads(self.assets_path.read_text(encoding="utf-8"))

    def write_manifest(self, value):
        temp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8")
        try:
            json.dump(value, temp)
            temp.close()
            return Path(temp.name)
        except Exception:
            temp.close()
            Path(temp.name).unlink(missing_ok=True)
            raise

    def assert_rejected(self, value, pattern):
        path = self.write_manifest(value)
        try:
            with self.assertRaisesRegex(DownloadError, pattern):
                load_asset_manifest(path)
        finally:
            path.unlink(missing_ok=True)

    def test_exact_three_asset_contract_is_pinned(self):
        assets = load_asset_manifest(self.assets_path)
        self.assertEqual({item["role"] for item in assets}, {"diffusion_model", "text_encoder", "vae"})
        checkpoint = next(item for item in assets if item["role"] == "diffusion_model")
        self.assertEqual(checkpoint["filename"], "miaomiaoHarem_29BBETA11.safetensors")
        self.assertEqual(checkpoint["sizeBytes"], 5_843_203_272)
        self.assertEqual(checkpoint["sha256"], "0fb5286099cb6059c5be09c1eb50d222ac6eae9626d761166b7514f8e38ded12")
        self.assertEqual(checkpoint["architectureBlocks"], 40)

    def test_empty_lora_catalog_is_only_allowed_explicitly(self):
        with self.assertRaisesRegex(DownloadError, "at least one"):
            load_lora_manifest(self.loras_path)
        self.assertEqual(load_lora_manifest(self.loras_path, allow_empty=True), [])

    def test_manifest_rejects_wrong_family_and_legacy_architecture(self):
        wrong_family = copy.deepcopy(self.assets)
        wrong_family["family"] = "Illustrious"
        self.assert_rejected(wrong_family, "family")
        legacy = copy.deepcopy(self.assets)
        legacy["assets"][0]["architectureBlocks"] = 28
        self.assert_rejected(legacy, "40-block")

    def test_manifest_rejects_missing_role_and_unpinned_checkpoint_version(self):
        missing = copy.deepcopy(self.assets)
        missing["assets"].pop()
        self.assert_rejected(missing, "exactly")
        wrong_version = copy.deepcopy(self.assets)
        wrong_version["checkpointVersionId"] = 1
        self.assert_rejected(wrong_version, "3360028")

    def test_manifest_rejects_paths_credentials_and_unapproved_hosts(self):
        for key, value, pattern in [
            ("filename", "../model.safetensors", "basename"),
            ("downloadUrl", "https://huggingface.co/file?token=secret", "credentials"),
            ("downloadUrl", "https://example.com/model.safetensors", "approved"),
        ]:
            changed = copy.deepcopy(self.assets)
            changed["assets"][0][key] = value
            self.assert_rejected(changed, pattern)

    def test_optional_loras_require_preconverted_40_block_artifacts(self):
        manifest = {
            "schemaVersion": 1,
            "family": "Anima-2.9B-40",
            "models": [{
                "id": "test-character",
                "filename": "test-character.safetensors",
                "family": "Anima-2.9B-40",
                "architectureBlocks": 40,
                "compatibility": "remapped-28-to-40",
                "downloadUrl": "https://civitai.com/api/download/models/1?fileId=2",
                "sha256": "a" * 64,
                "sizeBytes": 1024,
            }],
        }
        path = self.write_manifest(manifest)
        try:
            self.assertEqual(len(load_lora_manifest(path)), 1)
            manifest["models"][0]["architectureBlocks"] = 28
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(DownloadError, "40-block"):
                load_lora_manifest(path)
        finally:
            path.unlink(missing_ok=True)

    def test_dedicated_dockerfile_cannot_mutate_the_illustrious_image(self):
        dockerfile = (ROOT / "Dockerfile.anima").read_text(encoding="utf-8")
        self.assertIn("runpod/worker-comfyui:5.10.0-base@sha256:", dockerfile)
        self.assertIn("catalog/anima-assets.json", dockerfile)
        self.assertIn("catalog/anima-loras.json", dockerfile)
        self.assertIn("ANIMA-NOTICE.md", dockerfile)
        self.assertNotIn("catalog/character-loras.json", dockerfile)
        self.assertNotIn("waiNSFWIllustrious", dockerfile)


if __name__ == "__main__":
    unittest.main()
