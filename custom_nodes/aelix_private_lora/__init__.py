"""ComfyUI node for one owner-authorized private Anima LoRA."""

from .runtime import MAX_BLOB_BYTES, materialize_signed_lora


class AelixPrivateLoraLoader:
    """Fetch a single signed object, pin it, then use ComfyUI's standard loader."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "model": ("MODEL",),
                "clip": ("CLIP",),
                "signed_url": ("STRING", {"default": "", "multiline": False}),
                "sha256": ("STRING", {"default": "", "multiline": False}),
                "size_bytes": ("INT", {"default": 1, "min": 1, "max": MAX_BLOB_BYTES}),
                "strength_model": (
                    "FLOAT",
                    {"default": 1.0, "min": -4.0, "max": 4.0, "step": 0.01},
                ),
                "strength_clip": (
                    "FLOAT",
                    {"default": 1.0, "min": -4.0, "max": 4.0, "step": 0.01},
                ),
            }
        }

    RETURN_TYPES = ("MODEL", "CLIP")
    RETURN_NAMES = ("model", "clip")
    FUNCTION = "load_lora"
    CATEGORY = "Aelix/Private"

    def load_lora(self, model, clip, signed_url, sha256, size_bytes, strength_model, strength_clip):
        path = materialize_signed_lora(signed_url, sha256, size_bytes)
        # Lazy imports keep the security/cache helpers testable outside ComfyUI.
        import comfy.sd
        import comfy.utils

        lora = comfy.utils.load_torch_file(str(path), safe_load=True)
        return comfy.sd.load_lora_for_models(
            model,
            clip,
            lora,
            float(strength_model),
            float(strength_clip),
        )


NODE_CLASS_MAPPINGS = {"AelixPrivateLoraLoader": AelixPrivateLoraLoader}
NODE_DISPLAY_NAME_MAPPINGS = {"AelixPrivateLoraLoader": "Aelix Private Signed LoRA Loader"}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
