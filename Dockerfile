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

# Optional style trials use the owner's private, authenticated asset broker at
# runtime. No additional build-time weight downloads or credentials are needed.
COPY catalog/style-loras.json /opt/character-loras/style-loras.json
COPY scripts/style_bootstrap.py /opt/character-loras/style_bootstrap.py

# Disabled by default. Always delegates to the unchanged upstream /start.sh.
CMD ["python", "/opt/character-loras/style_bootstrap.py"]
