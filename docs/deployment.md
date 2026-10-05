# Character LoRA worker

This image retains `runpod/worker-comfyui:5.5.1-base`, the existing WAI Illustrious v15.0 checkpoint, the rgthree custom node package, and the existing Kim Possible filename. `catalog/character-loras.json` is the shared catalog for Docker installation and the character picker. All selected LoRAs must be compatible with Illustrious; SD 1.5, Pony, FLUX, and SDXL-only releases are not interchangeable with this checkpoint.

## Build and validation

```bash
python -m unittest discover -s tests -v
python scripts/download_loras.py --manifest catalog/character-loras.json --min-models 100 --validate-only
docker build --platform linux/amd64 -t api-lora-test:characters-v1 .
```

The first two commands do not download weights or use a GPU. The Docker build downloads the checkpoint and every catalog LoRA. Models live in `/comfyui/models/checkpoints` and `/comfyui/models/loras`. The original worker startup command and API envelope (`input.workflow`) are inherited unchanged.

The checkpoint has its own Docker layer so catalog changes can reuse it. LoRAs download directly into their final filesystem in a later layer, with two concurrent transfers and at least six seconds between request starts. There is no second model cache or runtime download. Every file must pass a pinned SHA256 check, an exact byte-size check when available, and Safetensors header checks before an atomic rename exposes its final filename. A missing/gated file, persistent rate limit, hash mismatch, HTML login page, or truncated file fails the image build. A failed file is never silently excluded.

Transfers make at most four attempts. All workers share HTTP 429 cooldowns and respect the full `Retry-After` value; without that header, rate-limit backoff starts at 30 seconds and doubles. A cooldown beyond the remaining 1,200-second installation budget stops the build without an early retry. Known Civitai daily-quota responses stop the queue immediately; pacing does not reset a daily quota. Interrupted `.part` files resume when the server supports HTTP ranges, and a server ignoring ranges causes a clean restart. Verified existing files are reused when running the installer against a persistent directory. Docker may discard an unsuccessful build layer, so resumption across separate hosted builds is not guaranteed.

`sizeBytes` is reserved for exact file sizes. Size estimates can be stored in separate metadata fields. URLs must be public HTTPS without API tokens or account credentials. Do not put Civitai or Runpod API keys in a catalog, Docker build argument, image layer, frontend bundle, or URL. The current files were individually available anonymously when curated; shared hosting IP quotas can still prevent a complete anonymous build.

## Runpod deployment

### Draft style weights and authenticated builds

The separate `catalog/style-loras.json` adds two style comparisons without
changing the checkpoint or the 103-character catalog. Fine Anime Screencap
Illustrious v3.0 uses `anime screencap, anime coloring` and a proposed initial
strength of 0.8; MeMaXL Illustrious v3.0 A lists no trigger and starts at 0.6.
These are trial settings, not verified image-quality recommendations. The
creator, exact version/file IDs, hash, permission metadata and links are pinned
in the manifest. File-size estimates are explicitly separate from verified
byte sizes. On October 5, both anonymous download requests returned HTTP 401.

The style layer therefore requires a BuildKit-mounted `civitai_token` secret.
The installer reads it without logging it, attaches authorization only to the
official HTTPS Civitai model-download path, and removes authorization on CDN
redirects. It reuses the existing SHA256/SafeTensors verifier and refuses a
partial installation. Do not put the token in Git, `ARG`, `ENV`, copied files,
download URL query parameters or image layers.

Offline validation requires no token and performs no downloads:

```bash
python -m unittest discover -s tests -v
python scripts/install_style_loras.py --manifest catalog/style-loras.json --validate-only
```

Already-owned local downloads can be installed without network access or
credentials. Keep each exact filename from the manifest in the source directory;
all selected files must pass complete SHA256 and SafeTensors verification before
any output is modified. Copies are verified again before atomic replacement:

```bash
python scripts/install_style_loras.py --manifest catalog/style-loras.json \
  --source-dir /private/style-weights --output-dir /comfyui/models/loras
```

The local-files option and `--token-file` are mutually exclusive. Do not commit
weights to Git or publish them as public release assets without confirming the
creator's redistribution permission; commercial image rights alone do not
establish a right to redistribute the raw weights.

On a builder with verified BuildKit secret support, an authorized operator can
provide a token from their existing secret store:

```bash
docker build --platform linux/amd64 \
  --secret id=civitai_token,env=CIVITAI_TOKEN \
  -t api-lora-test:style-tests .
```

The secret is used only while constructing the final style layer; it is not
needed at inference startup. The published Runpod GitHub-build documentation
does not establish BuildKit secret configuration. Do not create a GitHub
release to that builder until secret delivery is confirmed, or build using a
supported private secret mechanism and deploy the resulting versioned image.
Never replace this requirement with a plaintext token. A draft PR or successful
offline tests do not prove weight installation, a ready worker, or image quality.

The endpoint is `xjm75w7tf0ycci`. For GitHub-connected endpoints, a commit by itself does **not** deploy the new image: create a GitHub release for the configured repository/branch, then follow the endpoint's **Builds** tab through Building, Uploading, Testing, and Completed. Confirm the active image points to that release before exposing new choices to production users.

As checked on 2026-09-12, Runpod documents an **80 GB image limit**, a **30-minute Docker build step**, and a **160-minute total build/upload/test window**. Check the sum of catalog sizes plus the 6.94 GB checkpoint and the worker/CUDA dependencies against the image limit. Download pacing and a 20-minute catalog installation budget reserve time for image setup; actual completion still depends on upstream speed and quotas. The existing rgthree version is installed from its exact Git commit instead of fetching the entire Comfy node registry. If the build limit is exceeded, build the same Dockerfile on a machine with enough disk, publish a versioned image to a suitable container registry, and deploy that image through Runpod. Do not cut the catalog silently to make a failing build pass.

The endpoint's runtime writable disk and its image size are separate operational concerns. The installer writes model weights only during image construction; inference needs runtime room for outputs and temporary files. Watch build and worker logs before adjusting storage. Larger images also take longer to download on hosts where they are not already cached.

## API integration and smoke check

`example-request.json` is a complete single-pass, portrait-oriented workflow using core ComfyUI nodes. It fixes the previous missing CLIP links, invalid latent inputs, invalid LoRA input name, and mismatch between the Docker filename and request filename. It returns a saved image through the worker's `output.images` array.

To choose a character, set node `19` (`LoraLoader`) `inputs.lora_name` to that catalog entry's exact `filename`; set `strength_model` and `strength_clip` from the entry's recommendation or the application's defaults. Both prompt encoders use node `19`'s CLIP output, and the sampler uses its model output. Include the selected entry's trigger words in the positive prompt. For **Base model**, omit node `19` and connect the prompt encoders directly to checkpoint node `4`, output `1`, and the sampler to checkpoint node `4`, output `0`.

Submit the example through a server-side Runpod client or the Runpod console after the new worker becomes ready. A real `/run` or `/runsync` job consumes GPU time. Check that the job completes and that `output.images` contains the result. Test another catalog character and Base model, then confirm that the UI sends exact catalog filenames. A valid manifest and successful offline tests do not prove GPU inference succeeded.

Never expose the Runpod API key in browser code. The browser should send a selected catalog ID to your existing server; the server resolves it against its catalog and constructs the workflow. Loading filenames from an unrelated or stale catalog can cause ComfyUI's `value not in list` validation error.

## Sources

- [Runpod GitHub integration and current build limits](https://docs.runpod.io/serverless/workers/github-integration)
- [Runpod versioned Docker image deployment](https://docs.runpod.io/serverless/workers/deploy)
- [Worker ComfyUI 5.5.1 API format](https://github.com/runpod-workers/worker-comfyui/blob/5.5.1/README.md)
- [Worker ComfyUI 5.5.1 Dockerfile and installation paths](https://github.com/runpod-workers/worker-comfyui/blob/5.5.1/Dockerfile)
- [Runpod Serverless storage options](https://docs.runpod.io/serverless/storage/overview)
- [Exact retained rgthree version](https://github.com/rgthree/rgthree-comfy/blob/8ff50e4521881eca1fe26aec9615fc9362474931/pyproject.toml)
- [Civitai download quota counter](https://github.com/civitai/civitai/blob/1b2d7a9e4d0b5d608b46cc75f20d70dafc3eb90a/src/server/utils/download-count.ts)
