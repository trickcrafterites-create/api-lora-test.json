# Private MiaoMiao / Anima 2.9B worker

This is a separate image and deployment path. It does not modify the existing
Illustrious Dockerfile, checkpoint, 103-character catalog, endpoint, or graph.

## Offline validation

```bash
python -m unittest discover -s tests -v
python scripts/anima_install.py --kind assets --manifest catalog/anima-assets.json --validate-only
python scripts/anima_install.py --kind loras --manifest catalog/anima-loras.json --allow-empty --validate-only
```

Build without publishing or deploying:

```bash
docker build --platform linux/amd64 -f Dockerfile.anima -t aelix-anima:2.9b-beta1.1 .
```

The base image is pinned to RunPod worker-comfyui 5.10.0 / ComfyUI 0.34.0 by
OCI digest. The build downloads exactly three files: MiaoMiao version 3360028
to `models/diffusion_models`, the Anima Qwen 3 0.6B encoder to
`models/text_encoders`, and the Qwen-Image VAE to `models/vae`. Every file must
match both its exact byte length and SHA256 and pass a SafeTensors structural
check before an atomic rename exposes it.

`catalog/anima-loras.json` is intentionally empty. The base checkpoint works
without a LoRA. Only exact native 40-block files or files already converted
from 28 to 40 blocks may be added. Raw 28-block Anima, Illustrious, NoobAI,
Pony, SDXL, Flux, or other architectures fail manifest validation. Do not add
the runtime remapping patch unless the catalog and application contract are
separately changed to distinguish raw legacy files from converted ones.

## API workflow contract

Use core nodes only:

1. `UNETLoader` → optional `LoraLoader` → `ModelSamplingAuraFlow` (`shift: 3`).
2. `CLIPLoader` with `qwen_3_06b_base.safetensors`, type `stable_diffusion`.
3. `VAELoader` with `qwen_image_vae.safetensors`.
4. Euler + `sgm_uniform`, CFG 4, 28–50 steps.

The model card's 812-pixel recommendation is not native to ComfyUI's 8-pixel
latent grid: `EmptyLatentImage` floors width with `width // 8`, producing 808
pixels. Aelix uses 832×1216 and its transpose, plus 1152×1536 and its transpose.

Use a dedicated endpoint and set the web application server variable
`RUNPOD_ENDPOINT_ANIMA`. Never fall back to the Illustrious endpoint. Keep the
endpoint private and do not publish weights as GitHub release assets. Review
[ANIMA-NOTICE.md](../ANIMA-NOTICE.md) before any deployment or scope change.
