# clean base image containing only comfyui, comfy-cli and comfyui-manager
FROM runpod/worker-comfyui:5.5.1-base

# install custom nodes into comfyui (first node with --mode remote to fetch updated cache)
RUN comfy node install --exit-on-fail rgthree-comfy@1.0.2512112053 --mode remote

# download models into comfyui
RUN comfy model download --url https://huggingface.co/nnnn1111/models-moved/resolve/main/waiNSFWIllustrious_v150.safetensors --relative-path models/checkpoints --filename waiNSFWIllustrious_v150.safetensors

RUN comfy model download --url "https://www.dropbox.com/scl/fi/kosfszac2jmlq1sa25pgv/KimPossibleIllustrious2.0JLFO.safetensors?rlkey=h7s3ndipjhm9eu78fyxprtzqr&st=1uvfu8jv&dl=1" --relative-path models/loras --filename KimPossible-ArtStyle.safetensors
# copy all input data (like images or videos) into comfyui (uncomment and adjust if needed)
# COPY input/ /comfyui/input/
