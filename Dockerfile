# Keep the existing ComfyUI worker and checkpoint compatible with Illustrious LoRAs.
FROM runpod/worker-comfyui:5.5.1-base

# Retained for existing client workflows. The example uses only core ComfyUI nodes.
RUN comfy node install --exit-on-fail rgthree-comfy@1.0.2512112053 --mode remote

COPY scripts/download_loras.py /opt/character-loras/download_loras.py

# Cache the checkpoint separately so catalog updates do not download it again.
COPY catalog/checkpoint.json /opt/character-loras/checkpoint.json
RUN python /opt/character-loras/download_loras.py \
    --manifest /opt/character-loras/checkpoint.json \
    --output-dir /comfyui/models/checkpoints --workers 1

# One source of truth for exact filenames, public downloads, hashes, and UI metadata.
# Bounded parallelism fits large catalogs within Runpod's 30-minute Docker build window.
# Installation fails if even one file is missing, truncated, HTML, or has a wrong hash.
COPY catalog/character-loras.json /opt/character-loras/character-loras.json
RUN python /opt/character-loras/download_loras.py \
    --manifest /opt/character-loras/character-loras.json \
    --output-dir /comfyui/models/loras --min-models 100 --workers 4

# Inherit the upstream /start.sh command; no weights are downloaded on cold starts.
