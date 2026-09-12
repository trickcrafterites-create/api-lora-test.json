#!/usr/bin/env python3
"""Install a pinned model manifest. Python standard library only; no API keys needed."""

from __future__ import annotations

import argparse
import concurrent.futures
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

CHUNK_SIZE = 1024 * 1024
MAX_HEADER_BYTES = 100 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 16 * 1024**3
SAFE_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\.safetensors\Z")
SECRET_QUERY_KEYS = {"token", "access_token", "api_key", "apikey", "authorization"}


class DownloadError(RuntimeError):
    """An actionable, URL-free installation error safe to print in build logs."""


class IntegrityError(DownloadError):
    pass


def load_manifest(path: Path, min_models: int = 1) -> list[dict]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("schemaVersion") != 1:
        raise DownloadError("Manifest must have schemaVersion: 1")
    if manifest.get("baseModel") != "Illustrious":
        raise DownloadError("Manifest baseModel must be Illustrious for this checkpoint")
    models = manifest.get("models")
    if not isinstance(models, list) or len(models) < min_models:
        raise DownloadError(f"Manifest must contain at least {min_models} models")
    filenames, ids = set(), set()
    for index, model in enumerate(models):
        if not isinstance(model, dict):
            raise DownloadError(f"Manifest model {index} is not an object")
        model_id, filename = model.get("id"), model.get("filename")
        if not isinstance(model_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", model_id):
            raise DownloadError(f"Manifest model {index} has an invalid id")
        if not isinstance(filename, str) or not SAFE_FILENAME.fullmatch(filename):
            raise DownloadError(f"{model_id}: filename must be a safe .safetensors basename")
        if model_id in ids or filename.lower() in filenames:
            raise DownloadError(f"{model_id}: duplicate id or filename")
        ids.add(model_id)
        filenames.add(filename.lower())
        if not isinstance(model.get("sha256"), str) or not re.fullmatch(r"[a-fA-F0-9]{64}", model["sha256"]):
            raise DownloadError(f"{model_id}: a full SHA256 hash is required")
        url = model.get("downloadUrl")
        if not isinstance(url, str):
            raise DownloadError(f"{model_id}: downloadUrl is required")
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
            raise DownloadError(f"{model_id}: downloadUrl must be public HTTPS without credentials")
        if any(key.lower() in SECRET_QUERY_KEYS for key, _ in urllib.parse.parse_qsl(parsed.query)):
            raise DownloadError(f"{model_id}: do not put API tokens in catalog URLs")
        if "sizeBytes" in model and (type(model["sizeBytes"]) is not int or not 0 < model["sizeBytes"] <= MAX_DOWNLOAD_BYTES):
            raise DownloadError(f"{model_id}: sizeBytes must be an exact positive integer, at most 16 GiB")
        if model.get("baseModel", "Illustrious") != "Illustrious":
            raise DownloadError(f"{model_id}: incompatible base model")
    return models


def verify_file(path: Path, model: dict) -> None:
    if not path.is_file() or path.is_symlink():
        raise IntegrityError("file is missing or is a symlink")
    size = path.stat().st_size
    if "sizeBytes" in model and size != model["sizeBytes"]:
        raise IntegrityError(f"file size mismatch: expected {model['sizeBytes']}, got {size}")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        first = stream.read(8)
        if len(first) != 8:
            raise IntegrityError("file is empty or truncated")
        header_length = struct.unpack("<Q", first)[0]
        if not 2 <= header_length <= MAX_HEADER_BYTES or header_length + 8 >= size:
            raise IntegrityError("invalid Safetensors header (possibly an HTML error page)")
        header_bytes = stream.read(header_length)
        try:
            header = json.loads(header_bytes)
        except (ValueError, UnicodeDecodeError) as exc:
            raise IntegrityError("invalid Safetensors JSON header") from exc
        if not isinstance(header, dict) or not any(key != "__metadata__" for key in header):
            raise IntegrityError("Safetensors file contains no tensors")
        data_size = size - 8 - header_length
        for name, tensor in header.items():
            if name == "__metadata__":
                continue
            offsets = tensor.get("data_offsets") if isinstance(tensor, dict) else None
            if not isinstance(offsets, list) or len(offsets) != 2 or any(type(value) is not int for value in offsets):
                raise IntegrityError("invalid Safetensors tensor offsets")
            if not 0 <= offsets[0] <= offsets[1] <= data_size:
                raise IntegrityError("Safetensors tensor data is truncated")
        digest.update(first)
        digest.update(header_bytes)
        for chunk in iter(lambda: stream.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    if digest.hexdigest().lower() != model["sha256"].lower():
        raise IntegrityError("SHA256 mismatch; upstream file differs from the pinned catalog")


class HTTPSRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urlsplit(newurl)
        if target.scheme != "https" or target.username or target.password:
            raise DownloadError("refused an insecure download redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download_one(model: dict, output_dir: Path, retries: int = 4, timeout: int = 60,
                 retry_delay: float = 2, opener=None) -> str:
    """Resume temporary files, verify them, then atomically expose the final filename."""
    filename = model["filename"]
    destination = output_dir / filename
    partial = output_dir / f"{filename}.{model['sha256'][:12].lower()}.part"
    if destination.is_symlink() or partial.is_symlink():
        raise DownloadError(f"{model['id']}: refusing symlink destination")
    if destination.exists():
        try:
            verify_file(destination, model)
            return "cached"
        except IntegrityError:
            # Leave the old final file intact until its replacement passes validation.
            pass
    if partial.exists():
        try:
            verify_file(partial, model)
            os.replace(partial, destination)
            return "resumed"
        except IntegrityError:
            pass
    opener = opener or urllib.request.build_opener(HTTPSRedirectHandler())
    last_error = "download failed"
    for attempt in range(1, retries + 1):
        retry_after = None
        offset = partial.stat().st_size if partial.exists() else 0
        headers = {"User-Agent": "character-lora-installer/1.0", "Accept": "application/octet-stream", "Accept-Encoding": "identity"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        try:
            with opener.open(urllib.request.Request(model["downloadUrl"], headers=headers), timeout=timeout) as response:
                content_type = response.headers.get("Content-Type", "").lower()
                if any(kind in content_type for kind in ("text/html", "application/json", "text/plain", "application/xhtml")):
                    raise IntegrityError("server returned an error document instead of model weights")
                status = response.status
                if status == 206:
                    match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+|\*)", response.headers.get("Content-Range", ""))
                    if not match or int(match[1]) != offset:
                        raise IntegrityError("server returned an invalid resume range")
                elif status == 200:
                    offset = 0  # The server ignored Range: restart, never append a full file.
                else:
                    raise DownloadError(f"unexpected HTTP status {status}")
                length = response.headers.get("Content-Length")
                expected_length = int(length) if length is not None else None
                received = 0
                with partial.open("ab" if offset else "wb") as stream:
                    while True:
                        chunk = response.read(CHUNK_SIZE)
                        if not chunk:
                            break
                        stream.write(chunk)
                        received += len(chunk)
                        if offset + received > model.get("sizeBytes", MAX_DOWNLOAD_BYTES):
                            raise IntegrityError("download exceeded expected maximum size")
                    stream.flush()
                    os.fsync(stream.fileno())
                if expected_length is not None and received != expected_length:
                    raise http.client.IncompleteRead(b"", expected_length - received)
            verify_file(partial, model)
            os.replace(partial, destination)
            return "downloaded"
        except urllib.error.HTTPError as exc:
            last_error = f"HTTP {exc.code}"
            retry_header = exc.headers.get("Retry-After") if exc.headers else None
            if retry_header:
                try:
                    retry_after = float(retry_header)
                except ValueError:
                    try:
                        retry_after = (parsedate_to_datetime(retry_header) - datetime.now(timezone.utc)).total_seconds()
                    except (ValueError, TypeError, OverflowError):
                        pass
            if exc.code == 416:
                partial.unlink(missing_ok=True)
            elif exc.code not in (408, 429, 500, 502, 503, 504):
                raise DownloadError(f"{model['id']}: HTTP {exc.code}; verify public download access") from None
        except IntegrityError as exc:
            last_error = str(exc)
            partial.unlink(missing_ok=True)
        except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException, OSError) as exc:
            # Do not log exception strings: redirects may contain signed query parameters.
            last_error = type(exc).__name__
        if attempt < retries:
            print(f"Retry {attempt}/{retries - 1}: {model['id']} ({last_error})", flush=True)
            time.sleep(min(max(retry_after or 0, retry_delay * 2 ** (attempt - 1)), 60))
    raise DownloadError(f"{model['id']}: failed after {retries} attempts ({last_error})")


def install(models: list[dict], output_dir: Path, workers: int = 4, retries: int = 4, timeout: int = 60) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    total_bytes = sum(model.get("sizeBytes", 0) for model in models)
    print(f"Installing {len(models)} pinned models; known size {total_bytes / 1024**3:.2f} GiB; concurrency {workers}", flush=True)
    failures = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(download_one, model, output_dir, retries, timeout): model for model in models}
        for completed, future in enumerate(concurrent.futures.as_completed(futures), 1):
            model = futures[future]
            try:
                result = future.result()
                print(f"[{completed}/{len(models)}] {model['id']}: {result}", flush=True)
            except Exception as exc:
                error = str(exc) if isinstance(exc, DownloadError) else type(exc).__name__
                failures.append(error)
                print(f"[{completed}/{len(models)}] ERROR {model['id']}: {error}", file=sys.stderr, flush=True)
    if failures:
        raise DownloadError(f"{len(failures)} model(s) failed; refusing an incomplete image. " + "; ".join(failures))
    print(f"Verified and installed all {len(models)} models.", flush=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--min-models", type=int, default=1)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--retries", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--validate-only", action="store_true", help="Validate metadata without downloading weights")
    args = parser.parse_args(argv)
    if not 1 <= args.workers <= 16 or not 1 <= args.retries <= 10 or args.timeout < 1 or args.min_models < 1:
        parser.error("workers must be 1..16; retries 1..10; timeout and min-models must be positive")
    if not args.validate_only and not args.output_dir:
        parser.error("--output-dir is required unless --validate-only is supplied")
    try:
        models = load_manifest(args.manifest, args.min_models)
        if args.validate_only:
            print(f"Valid manifest: {len(models)} pinned Illustrious models")
        else:
            install(models, args.output_dir, args.workers, args.retries, args.timeout)
        return 0
    except (DownloadError, OSError, ValueError) as exc:
        print(f"Model installation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
