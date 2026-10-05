# syntax=docker/dockerfile:1
# Keep the existing ComfyUI worker and checkpoint compatible with Illustrious LoRAs.
FROM runpod/worker-comfyui:5.5.1-base

# Retain the exact existing rgthree version without fetching the entire node registry.
# Upstream pyproject.toml at this commit declares version 1.0.2512112053 and no dependencies.
RUN git init /comfyui/custom_nodes/rgthree-comfy \
    && git -C /comfyui/custom_nodes/rgthree-comfy remote add origin https://github.com/rgthree/rgthree-comfy.git \
    && git -C /comfyui/custom_nodes/rgthree-comfy fetch --depth 1 origin 8ff50e4521881eca1fe26aec9615fc9362474931 \
    && git -C /comfyui/custom_nodes/rgthree-comfy checkout --detach FETCH_HEAD

COPY scripts/download_loras.py /opt/character-loras/download_loras.py

# Cache the checkpoint separately so catalog updates do not download it again.
COPY catalog/checkpoint.json /opt/character-loras/checkpoint.json
RUN python /opt/character-loras/download_loras.py \
    --manifest /opt/character-loras/checkpoint.json \
    --output-dir /comfyui/models/checkpoints --workers 1

# One source of truth for exact filenames, public downloads, hashes, and UI metadata.
# Space request starts and share server cooldowns across both transfer workers.
# Installation fails if even one file is missing, truncated, HTML, or has a wrong hash.
COPY catalog/character-loras.json /opt/character-loras/character-loras.json
RUN python /opt/character-loras/download_loras.py \
    --manifest /opt/character-loras/character-loras.json \
    --output-dir /comfyui/models/loras --min-models 100 --workers 2 \
    --min-interval 6 --max-elapsed 1200

# Keep style experiments in their own layer after the existing pinned catalog.
# Civitai requires authentication for these files. Mount a BuildKit secret; never
# pass the token through ARG, ENV, a committed URL, or a copied credentials file.
COPY scripts/install_style_loras.py /opt/character-loras/install_style_loras.py
COPY catalog/style-loras.json /opt/character-loras/style-loras.json
RUN --mount=type=secret,id=civitai_token,required=true \
    python /opt/character-loras/install_style_loras.py \
    --manifest /opt/character-loras/style-loras.json \
    --output-dir /comfyui/models/loras \
    --token-file /run/secrets/civitai_token

# Inherit the upstream /start.sh command; no weights are downloaded on cold starts.
