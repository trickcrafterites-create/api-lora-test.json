#!/usr/bin/env python3
"""Validate and install the isolated Anima 2.9B worker assets."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
import sys
import urllib.parse

from download_loras import (
    DownloadError,
    MAX_DOWNLOAD_BYTES,
    RequestStartLimiter,
    SAFE_FILENAME,
    SECRET_QUERY_KEYS,
    download_one,
)

FAMILY = "Anima-2.9B-40"
ROLE_DIRS = {
    "diffusion_model": "diffusion_models",
    "text_encoder": "text_encoders",
    "vae": "vae",
}
EXPECTED_ASSET_ROLES = frozenset(ROLE_DIRS)
ALLOWED_DOWNLOAD_HOSTS = frozenset({"civitai.com", "huggingface.co"})
SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
LORA_COMPATIBILITY = frozenset({"native-40", "remapped-28-to-40"})
CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f-\x9f]")
MAX_ACTIVATION_PHRASES = 20
MAX_ACTIVATION_PHRASE_CHARS = 1000


def _read_manifest(path: Path) -> dict:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("schemaVersion") != 1:
        raise DownloadError("Manifest must have schemaVersion: 1")
    if manifest.get("family") != FAMILY:
        raise DownloadError(f"Manifest family must be {FAMILY}")
    return manifest


def _validate_download(model: dict, index: int, seen_ids: set[str], seen_names: set[str]) -> None:
    if not isinstance(model, dict):
        raise DownloadError(f"Manifest entry {index} is not an object")
    model_id, filename = model.get("id"), model.get("filename")
    if not isinstance(model_id, str) or not SAFE_ID.fullmatch(model_id):
        raise DownloadError(f"Manifest entry {index} has an invalid id")
    if not isinstance(filename, str) or not SAFE_FILENAME.fullmatch(filename):
        raise DownloadError(f"{model_id}: filename must be a safe .safetensors basename")
    if model_id in seen_ids or filename.lower() in seen_names:
        raise DownloadError(f"{model_id}: duplicate id or filename")
    seen_ids.add(model_id)
    seen_names.add(filename.lower())
    if model.get("family") != FAMILY:
        raise DownloadError(f"{model_id}: incompatible model family")
    if not isinstance(model.get("sha256"), str) or not re.fullmatch(r"[a-fA-F0-9]{64}", model["sha256"]):
        raise DownloadError(f"{model_id}: a full SHA256 hash is required")
    if type(model.get("sizeBytes")) is not int or not 0 < model["sizeBytes"] <= MAX_DOWNLOAD_BYTES:
        raise DownloadError(f"{model_id}: exact sizeBytes is required and must be at most 16 GiB")
    url = model.get("downloadUrl")
    if not isinstance(url, str):
        raise DownloadError(f"{model_id}: downloadUrl is required")
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname not in ALLOWED_DOWNLOAD_HOSTS
            or parsed.username or parsed.password or parsed.fragment):
        raise DownloadError(f"{model_id}: downloadUrl must use an approved public HTTPS host")
    if any(key.lower() in SECRET_QUERY_KEYS for key, _ in urllib.parse.parse_qsl(parsed.query)):
        raise DownloadError(f"{model_id}: do not put credentials in download URLs")


def load_asset_manifest(path: Path) -> list[dict]:
    manifest = _read_manifest(path)
    if manifest.get("checkpointModelId") != 934764 or manifest.get("checkpointVersionId") != 3360028:
        raise DownloadError("Asset manifest must pin MiaoMiao model 934764 version 3360028")
    if manifest.get("comfyuiMinVersion") != "0.34.0":
        raise DownloadError("Asset manifest must pin the reviewed ComfyUI 0.34.0 contract")
    assets = manifest.get("assets")
    if not isinstance(assets, list) or len(assets) != len(EXPECTED_ASSET_ROLES):
        raise DownloadError("Asset manifest must contain exactly the diffusion model, text encoder and VAE")
    seen_ids, seen_names, roles = set(), set(), set()
    for index, asset in enumerate(assets):
        _validate_download(asset, index, seen_ids, seen_names)
        role = asset.get("role")
        if role not in ROLE_DIRS or role in roles:
            raise DownloadError(f"{asset['id']}: invalid or duplicate asset role")
        roles.add(role)
        if role == "diffusion_model" and asset.get("architectureBlocks") != 40:
            raise DownloadError("MiaoMiao checkpoint must declare the 40-block Anima 2.9B architecture")
    if roles != EXPECTED_ASSET_ROLES:
        raise DownloadError("Asset manifest is missing a required model role")
    return assets


def load_lora_manifest(path: Path, allow_empty: bool = False) -> list[dict]:
    manifest = _read_manifest(path)
    models = manifest.get("models")
    if not isinstance(models, list) or (not models and not allow_empty):
        raise DownloadError("LoRA manifest must contain at least one model")
    seen_ids, seen_names, seen_versions, seen_file_ids = set(), set(), set(), set()
    for index, model in enumerate(models):
        _validate_download(model, index, seen_ids, seen_names)
        if model.get("architectureBlocks") != 40:
            raise DownloadError(f"{model['id']}: only 40-block Anima 2.9B LoRA files may be installed")
        if model.get("compatibility") not in LORA_COMPATIBILITY:
            raise DownloadError(f"{model['id']}: compatibility must be native-40 or remapped-28-to-40")
        for field in ("modelId", "modelVersionId", "fileId"):
            value = model.get(field)
            if type(value) is not int or value <= 0:
                raise DownloadError(f"{model['id']}: exact positive {field} is required")
        if model["modelVersionId"] in seen_versions or model["fileId"] in seen_file_ids:
            raise DownloadError(f"{model['id']}: duplicate Civitai version or file identity")
        seen_versions.add(model["modelVersionId"])
        seen_file_ids.add(model["fileId"])
        for field in ("name", "creator"):
            value = model.get(field)
            if (not isinstance(value, str) or not value or value != value.strip()
                    or len(value) > 200 or CONTROL_CHARACTERS.search(value)):
                raise DownloadError(f"{model['id']}: valid {field} metadata is required")
        phrases = model.get("activationPhrases")
        if not isinstance(phrases, list) or not 1 <= len(phrases) <= MAX_ACTIVATION_PHRASES:
            raise DownloadError(f"{model['id']}: activationPhrases must contain 1..{MAX_ACTIVATION_PHRASES} values")
        if len(set(phrases)) != len(phrases):
            raise DownloadError(f"{model['id']}: activationPhrases must be unique")
        for phrase in phrases:
            if (not isinstance(phrase, str) or not phrase or phrase != phrase.strip()
                    or len(phrase) > MAX_ACTIVATION_PHRASE_CHARS or CONTROL_CHARACTERS.search(phrase)):
                raise DownloadError(f"{model['id']}: invalid activation phrase metadata")
        parsed = urllib.parse.urlsplit(model["downloadUrl"])
        match = re.fullmatch(r"/api/download/models/(\d+)", parsed.path)
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        if (parsed.hostname != "civitai.com" or not match
                or int(match.group(1)) != model["modelVersionId"]
                or query != {"fileId": [str(model["fileId"])]}):
            raise DownloadError(f"{model['id']}: downloadUrl must pin the exact Civitai version and fileId")

    unresolved = manifest.get("unresolved", [])
    if not isinstance(unresolved, list):
        raise DownloadError("LoRA manifest unresolved field must be a list")
    for index, item in enumerate(unresolved):
        if not isinstance(item, dict):
            raise DownloadError(f"Unresolved LoRA entry {index} is not an object")
        item_id, filename = item.get("id"), item.get("filename")
        if not isinstance(item_id, str) or not SAFE_ID.fullmatch(item_id) or item_id in seen_ids:
            raise DownloadError(f"Unresolved LoRA entry {index} has an invalid or duplicate id")
        if not isinstance(filename, str) or not SAFE_FILENAME.fullmatch(filename):
            raise DownloadError(f"{item_id}: unresolved filename must be a safe .safetensors basename")
        for field in ("modelId", "modelVersionId", "fileId", "sizeBytes"):
            value = item.get(field)
            if type(value) is not int or value <= 0:
                raise DownloadError(f"{item_id}: exact positive {field} is required")
        if (not isinstance(item.get("sha256"), str)
                or not re.fullmatch(r"[a-fA-F0-9]{64}", item["sha256"])):
            raise DownloadError(f"{item_id}: a full unresolved SHA256 hash is required")
        reason = item.get("reason")
        if (not isinstance(reason, str) or not reason or reason != reason.strip()
                or len(reason) > 500 or CONTROL_CHARACTERS.search(reason)):
            raise DownloadError(f"{item_id}: a safe unresolved reason is required")
        if "downloadUrl" in item:
            raise DownloadError(f"{item_id}: unresolved entries must not contain a download URL")
        seen_ids.add(item_id)
    return models


def install_entries(entries: list[dict], output_root: Path, kind: str, retries: int,
                    timeout: int, min_interval: float, max_elapsed: float) -> None:
    if not entries:
        print("No optional Anima LoRAs are pinned; building the base-only worker.", flush=True)
        return
    limiter = RequestStartLimiter(min_interval=min_interval, max_elapsed=max_elapsed)
    total = sum(entry["sizeBytes"] for entry in entries)
    print(f"Installing {len(entries)} pinned Anima {kind}; exact size {total / 1024**3:.2f} GiB", flush=True)
    for index, entry in enumerate(entries, 1):
        subdir = ROLE_DIRS[entry["role"]] if kind == "assets" else "loras"
        destination = output_root / subdir
        destination.mkdir(parents=True, exist_ok=True)
        result = download_one(entry, destination, retries=retries, timeout=timeout, limiter=limiter)
        print(f"[{index}/{len(entries)}] {entry['id']}: {result}", flush=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--kind", choices=("assets", "loras"), required=True)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--allow-empty", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--retries", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--min-interval", type=float, default=5)
    parser.add_argument("--max-elapsed", type=float, default=1500)
    args = parser.parse_args(argv)
    if not 1 <= args.retries <= 10 or args.timeout < 1:
        parser.error("retries must be 1..10 and timeout must be positive")
    if (not math.isfinite(args.min_interval) or args.min_interval < 0
            or not math.isfinite(args.max_elapsed) or args.max_elapsed <= 0):
        parser.error("download timing values must be finite and nonnegative/positive")
    if not args.validate_only and not args.output_root:
        parser.error("--output-root is required unless --validate-only is supplied")
    try:
        entries = (load_asset_manifest(args.manifest) if args.kind == "assets"
                   else load_lora_manifest(args.manifest, allow_empty=args.allow_empty))
        if args.validate_only:
            print(f"Valid pinned Anima manifest: {len(entries)} {args.kind}")
        else:
            install_entries(entries, args.output_root, args.kind, args.retries,
                            args.timeout, args.min_interval, args.max_elapsed)
        return 0
    except (DownloadError, OSError, ValueError) as exc:
        print(f"Anima installation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
