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
        self.loras = json.loads(self.loras_path.read_text(encoding="utf-8"))

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

    def assert_lora_rejected(self, value, pattern):
        path = self.write_manifest(value)
        try:
            with self.assertRaisesRegex(DownloadError, pattern):
                load_lora_manifest(path)
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

    def test_exact_public_lora_catalog_and_auth_gated_exception_are_pinned(self):
        models = load_lora_manifest(self.loras_path)
        self.assertEqual(len(models), 39)
        self.assertEqual(sum(item["sizeBytes"] for item in models), 4_078_862_048)
        self.assertEqual(len({item["modelVersionId"] for item in models}), 39)
        self.assertEqual(len({item["fileId"] for item in models}), 39)
        self.assertNotIn(3227485, {item["modelVersionId"] for item in models})
        juno = next(item for item in models if item["id"] == "juno-beastars")
        self.assertEqual(juno["activationPhrases"], ["junobeastars"])
        self.assertEqual(juno["sha256"], "2f4177063d3f888fd5c4b6a7b632f70e3f695d4e101e03a081f1a1c6723ba12b")
        self.assertEqual(self.loras["unresolved"], [{
            "id": "hinako-issho-training",
            "name": "Hinako",
            "modelId": 854149,
            "modelVersionId": 3227485,
            "fileId": 3109704,
            "filename": "hinako_issho_ni_training_Anima-2.9B-preview-v1_v1.safetensors",
            "sizeBytes": 131_231_448,
            "sha256": "282ad28c5eea46d0a7b5f93033aaa20136fd38f642a0d3f10466bcaad8b56dfb",
            "reason": "The exact Civitai download returns HTTP 401 without an account bearer token. Keep credentials out of manifests, URLs, image layers and build logs.",
        }])

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
                "modelId": 3,
                "modelVersionId": 1,
                "fileId": 2,
                "name": "Test Character",
                "creator": "Test Creator",
                "activationPhrases": ["test_character"],
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

    def test_lora_metadata_fails_closed(self):
        for mutate, pattern in [
            (lambda model: model.update(downloadUrl="https://civitai.com/api/download/models/9?fileId=2"), "exact Civitai"),
            (lambda model: model.update(activationPhrases=["same", "same"]), "unique"),
            (lambda model: model.update(modelVersionId=0), "modelVersionId"),
        ]:
            changed = copy.deepcopy(self.loras)
            mutate(changed["models"][0])
            self.assert_lora_rejected(changed, pattern)
        changed = copy.deepcopy(self.loras)
        changed["unresolved"][0]["downloadUrl"] = "https://civitai.com/api/download/models/3227485?token=secret"
        self.assert_lora_rejected(changed, "must not contain a download URL")

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
