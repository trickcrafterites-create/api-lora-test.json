#!/usr/bin/env python3
"""Discover candidates or refresh a manually curated, version-pinned character catalog.

Uses the public Civitai REST API. Does not download complete model weights.
The installer performs the full SHA256 verification when it installs each model.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
API = "https://civitai.com/api/v1"
BASE_MODEL = "Illustrious"
TIMEOUT = 45


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def fetch_json(url: str) -> dict:
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "character-lora-catalog/1.0"})
    try:
        with urlopen(request, timeout=TIMEOUT) as response:
            return json.load(response)
    except HTTPError as exc:
        if exc.code == 429:
            raise RuntimeError("Civitai rate limited this run; stop and retry later. Existing catalog is unchanged.") from exc
        raise RuntimeError(f"Metadata request returned HTTP {exc.code}: {url}") from exc


def verify_download(url: str) -> tuple[int, dict]:
    """Read eight bytes and close the response, even when a server ignores Range."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in {"civitai.com", "civitai.red"}:
        raise ValueError("Expected a public HTTPS Civitai version-download URL")
    request = Request(url, headers={"Range": "bytes=0-7", "User-Agent": "character-lora-catalog/1.0"})
    try:
        with urlopen(request, timeout=TIMEOUT) as response:
            prefix = response.read(8)
            content_type = response.headers.get("Content-Type", "")
            if response.status not in (200, 206) or len(prefix) != 8:
                raise ValueError("Download did not return model bytes")
            if "json" in content_type or "html" in content_type:
                raise ValueError("Download returned a page instead of model bytes")
            header_length = int.from_bytes(prefix, "little")
            if not 2 < header_length < 50_000_000:
                raise ValueError("Download does not begin with a plausible SafeTensors header")
            if response.status == 206:
                match = re.fullmatch(r"bytes 0-7/(\d+)", response.headers.get("Content-Range", ""))
                if not match:
                    raise ValueError("Range response has an unexpected Content-Range")
                size = int(match[1])
            else:
                size = int(response.headers["Content-Length"])
            return size, {
                "checkedAt": now(),
                "method": "Unauthenticated HTTP GET with Range: bytes=0-7",
                "httpStatus": response.status,
                "contentType": content_type,
                "safetensorsHeaderLength": header_length,
                "sha256Source": "Civitai file metadata; full bytes checked during installation",
            }
    except HTTPError as exc:
        if exc.code == 429:
            raise RuntimeError("Civitai rate limited downloads; stop and retry later. Existing catalog is unchanged.") from exc
        if exc.code in (401, 403):
            raise RuntimeError("This model is no longer anonymously downloadable; existing catalog is unchanged.") from exc
        raise


def validate(catalog: dict, minimum: int = 100) -> None:
    models = catalog["models"]
    if catalog.get("schemaVersion") != 1 or catalog.get("baseModel") != BASE_MODEL:
        raise ValueError("Wrong catalog schema or base model")
    if len(models) < minimum:
        raise ValueError(f"Only {len(models)} models; expected at least {minimum}")
    civitai_models = [m for m in models if m["id"].startswith("civitai-")]
    if len(civitai_models) < minimum:
        raise ValueError(f"Only {len(civitai_models)} newly selected Civitai characters; expected {minimum}")
    if len({m["modelId"] for m in civitai_models}) != len(civitai_models):
        raise ValueError("Duplicate Civitai modelId")
    for key in ("id", "filename", "name", "sha256"):
        if len({m[key] for m in models}) != len(models):
            raise ValueError(f"Duplicate {key} in catalog")
    for model in models:
        if model["baseModel"] != BASE_MODEL:
            raise ValueError(f"Wrong base model: {model['name']}")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*\.safetensors", model["filename"]):
            raise ValueError(f"Unsafe model filename: {model['filename']}")
        if not re.fullmatch(r"[0-9a-f]{64}", model["sha256"]):
            raise ValueError(f"Invalid SHA256: {model['name']}")
        if model["id"].startswith("civitai-") and "Rent" not in model["license"].get("allowCommercialUse", []):
            raise ValueError(f"Third-party generation service permission absent: {model['name']}")
        if not model["id"].startswith("civitai-") and model["id"] != "kim-possible-legacy":
            raise ValueError("Unknown non-Civitai entry; review before allowing a legacy exception")
        if not isinstance(model["sizeBytes"], int) or model["sizeBytes"] <= 8:
            raise ValueError(f"Invalid model size: {model['name']}")
        if model["verification"]["httpStatus"] not in (200, 206):
            raise ValueError(f"Unverified download: {model['name']}")
        if not isinstance(model["triggerWords"], list) or not all(isinstance(word, str) for word in model["triggerWords"]):
            raise ValueError(f"Invalid activation tokens: {model['name']}")
    if catalog.get("modelCount") != len(models):
        raise ValueError("modelCount does not match models")
    if catalog.get("totalSizeBytes") != sum(m["sizeBytes"] for m in models):
        raise ValueError("totalSizeBytes does not match model sizes")


def discover(args: argparse.Namespace) -> None:
    """Export compact candidates; a person must select distinct character identities."""
    query = urlencode({"limit": 100, "types": "LORA", "tag": "character", "baseModels": BASE_MODEL,
                       "nsfw": "false", "sort": "Most Downloaded", "period": "AllTime"})
    url = f"{API}/models?{query}"
    candidates = []
    seen = set()
    for page in range(args.pages):
        data = fetch_json(url)
        for model in data["items"]:
            if model["id"] in seen or model.get("poi") or model["type"] != "LORA":
                continue
            seen.add(model["id"])
            if "Rent" not in model.get("allowCommercialUse", []):
                continue
            for version in model["modelVersions"]:
                if version["baseModel"] != BASE_MODEL or version["availability"] != "Public":
                    continue
                for file in version["files"]:
                    if (file["type"] != "Model" or file.get("metadata", {}).get("format") != "SafeTensor"
                            or not file.get("primary") or not file.get("hashes", {}).get("SHA256")
                            or file["sizeKB"] > args.max_mb * 1024):
                        continue
                    candidates.append({"modelId": model["id"], "sourceModelName": model["name"],
                        "modelVersionId": version["id"], "versionName": version["name"], "fileId": file["id"],
                        "baseModel": version["baseModel"], "creator": model["creator"]["username"],
                        "downloadUrl": file["downloadUrl"], "sha256": file["hashes"]["SHA256"].lower(),
                        "sizeKB": file["sizeKB"], "allowCommercialUse": model["allowCommercialUse"]})
                    break
        print(f"Page {page + 1}: {len(candidates)} candidate versions", flush=True)
        url = data.get("metadata", {}).get("nextPage")
        if not url:
            break
        if urlparse(url).hostname != "civitai.com":
            raise ValueError("Unexpected pagination host")
        time.sleep(args.delay)
    write_json(args.output, {"generatedAt": now(), "candidates": candidates,
        "note": "Candidates are not curated or download-verified. Public metadata may still require authenticated downloads."})


def refresh(args: argparse.Namespace) -> None:
    catalog = read_json(args.catalog)
    seeds = read_json(args.seeds)["characters"]
    existing = {m["id"]: m for m in catalog["models"]}
    # Keep the user's existing, separately verified Kim model outside Civitai refresh.
    models = [m for m in catalog["models"] if m["id"] == "kim-possible-legacy"]
    for index, seed in enumerate(seeds, 1):
        model = fetch_json(f"{API}/models/{seed['modelId']}")
        if model["type"] != "LORA" or model.get("poi") or "Rent" not in model["allowCommercialUse"]:
            raise ValueError(f"Model no longer meets selection criteria: {seed['name']}")
        version = next(v for v in model["modelVersions"] if v["id"] == seed["modelVersionId"])
        file = next(f for f in version["files"] if f["id"] == seed["fileId"])
        if version["baseModel"] != BASE_MODEL or version["availability"] != "Public":
            raise ValueError(f"Version no longer meets selection criteria: {seed['name']}")
        if file["type"] != "Model" or file["metadata"]["format"] != "SafeTensor" or not file.get("primary"):
            raise ValueError(f"File no longer meets selection criteria: {seed['name']}")
        identity = f"civitai-{model['id']}-{version['id']}"
        prior = existing.get(identity)
        sha = file["hashes"]["SHA256"].lower()
        if prior and prior["sha256"] != sha:
            raise ValueError(f"Pinned hash changed unexpectedly: {seed['name']}")
        size, verification = verify_download(file["downloadUrl"])
        if prior and prior["sizeBytes"] != size:
            raise ValueError(f"Pinned size changed unexpectedly: {seed['name']}")
        stem = re.sub(r"[^a-z0-9]+", "-", seed["name"].lower()).strip("-")
        entry = {"id": identity, "name": seed["name"], "franchise": seed["franchise"],
                 "filename": f"{stem}-{version['id']}.safetensors", "baseModel": BASE_MODEL,
                 "triggerWords": seed["triggerWords"], "strength": seed["strength"],
                 "strengthNote": "Starting value; adjust per prompt and checkpoint.",
                 "modelId": model["id"], "modelVersionId": version["id"], "fileId": file["id"],
                 "sourceUrl": f"https://civitai.red/models/{model['id']}?modelVersionId={version['id']}",
                 "metadataUrl": f"{API}/models/{model['id']}", "sourceModelName": model["name"],
                 "versionName": version["name"], "creator": model["creator"]["username"],
                 "downloadUrl": file["downloadUrl"], "sha256": sha, "sizeBytes": size,
                 "license": {k: model[k] for k in ("allowNoCredit", "allowCommercialUse", "allowDerivatives", "allowDifferentLicense")},
                 "availability": version["availability"], "verification": verification}
        models.append(entry)
        print(f"Verified {index}/{len(seeds)}: {seed['name']}", flush=True)
        time.sleep(args.delay)
    catalog.update(models=sorted(models, key=lambda x: x["name"].casefold()), modelCount=len(models),
                   civitaiModelCount=len(seeds), legacyModelCount=len(models) - len(seeds),
                   totalSizeBytes=sum(m["sizeBytes"] for m in models), generatedAt=now())
    validate(catalog)
    write_json(args.catalog, catalog)
    print(f"Saved {len(models)} verified entries; full weights will be hashed at installation.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    discovery = sub.add_parser("discover", help="Fetch metadata candidates for manual curation")
    discovery.add_argument("--pages", type=int, default=8)
    discovery.add_argument("--max-mb", type=float, default=350)
    discovery.add_argument("--delay", type=float, default=2)
    discovery.add_argument("--output", type=Path, required=True)
    discovery.set_defaults(action=discover)
    refresh_parser = sub.add_parser("refresh", help="Recheck pinned seeds and atomically refresh catalog")
    refresh_parser.add_argument("--catalog", type=Path, default=ROOT / "catalog/character-loras.json")
    refresh_parser.add_argument("--seeds", type=Path, default=ROOT / "catalog/character-seeds.json")
    refresh_parser.add_argument("--delay", type=float, default=2)
    refresh_parser.set_defaults(action=refresh)
    validation = sub.add_parser("validate", help="Validate the local manifest without network requests")
    validation.add_argument("--catalog", type=Path, default=ROOT / "catalog/character-loras.json")
    validation.set_defaults(action=lambda args: (validate(read_json(args.catalog)), print("Catalog valid.")))
    args = parser.parse_args()
    if hasattr(args, "delay") and args.delay < 1:
        parser.error("--delay must be at least one second")
    if hasattr(args, "pages") and not 1 <= args.pages <= 50:
        parser.error("--pages must be between 1 and 50")
    args.action(args)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, HTTPError, KeyError, StopIteration) as exc:
        sys.exit(f"Catalog collection failed: {exc}")
