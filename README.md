# Illustrious character LoRA endpoint

Runpod ComfyUI worker with **103 character LoRAs**: 102 curated Civitai characters plus the existing Kim Possible model. The Docker image includes the checkpoint and character weights; optional style trials can use a private startup download.

The [catalog](catalog/character-loras.json) pins compatible Illustrious files, exact filenames, hashes, sizes, activation words, creators and source permissions. The installer fails if any model is missing or fails validation. LoRA weights total 15.43 GB; the existing checkpoint adds 6.94 GB, before the base image.

```bash
python -m unittest discover -s tests -v
python scripts/download_loras.py --manifest catalog/character-loras.json --min-models 100 --validate-only
docker build --platform linux/amd64 -t api-lora-test:characters-v1 .
```

Use [example-request.json](example-request.json) for a complete request. Your application should resolve a character ID server-side to its catalog filename and trigger words. Never send the Runpod API key to the browser.

See [deployment instructions](docs/deployment.md) for build limits, GitHub release deployment, validation, and the workflow contract. Public file availability was checked during curation; the Docker build verifies each complete download. Catalog validation alone does not establish inference quality.

## Draft style comparison

`catalog/style-loras.json` pins Fine Anime Screencap Illustrious v3.0 and MeMaXL
Illustrious v3.0 A. The user-provided local files passed SHA256 and SafeTensors
verification. The optional runtime bootstrap retrieves these exact files from
the owner's private Vercel storage through a scoped Aelixhub broker. It does not
connect to Civitai or require build secrets. No token or model binary belongs in Git.

The existing checkpoint, all 103 character entries and their Docker layers are
unchanged. Style loading defaults off. Failure to load an optional style still
starts the original worker for existing generation requests. The styles have
not yet been installed on the hosted worker or tested by inference. See the
deployment notes for the exact runtime contract and enable only after review.
