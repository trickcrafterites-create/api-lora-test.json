# Illustrious character LoRA endpoint

Runpod ComfyUI worker with **103 character LoRAs**: 102 curated Civitai characters plus the existing Kim Possible model. The Docker image includes all weights; inference does not download models at startup.

The [catalog](catalog/character-loras.json) pins compatible Illustrious files, exact filenames, hashes, sizes, activation words, creators and source permissions. The installer fails if any model is missing or fails validation. LoRA weights total 15.43 GB; the existing checkpoint adds 6.94 GB, before the base image.

```bash
python -m unittest discover -s tests -v
python scripts/download_loras.py --manifest catalog/character-loras.json --min-models 100 --validate-only
docker build --platform linux/amd64 -t api-lora-test:characters-v1 .
```

Use [example-request.json](example-request.json) for a complete request. Your application should resolve a character ID server-side to its catalog filename and trigger words. Never send the Runpod API key to the browser.

See [deployment instructions](docs/deployment.md) for build limits, GitHub release deployment, validation, and the workflow contract. Public file availability was checked during curation; the Docker build verifies each complete download. Catalog validation alone does not establish inference quality.
