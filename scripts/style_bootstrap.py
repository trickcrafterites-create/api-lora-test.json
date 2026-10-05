#!/usr/bin/env python3
"""Optional verified style downloads from the owner's private asset broker."""

from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import download_loras as downloader

BROKER_BASE = "https://aelixhub.red/api/style-tests/assets"
PINNED_IDS = {"style-civitai-345962-2553688", "style-civitai-269772-919342"}
ASSET_HOST = re.compile(r"[a-zA-Z0-9_-]+\.private\.blob\.vercel-storage\.com\Z")
MAX_BROKER_BYTES = 16 * 1024
MIN_FREE_BYTES = 512 * 1024**2
BOOTSTRAP_BUDGET_SECONDS = 120


class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise downloader.DownloadError("Style asset redirects are not allowed")


def configuration(environ):
    base = environ.get("AELIX_STYLE_BROKER_BASE", "")
    token = environ.get("AELIX_STYLE_BROKER_TOKEN", "")
    host = environ.get("AELIX_STYLE_ASSET_HOST", "")
    if base != BROKER_BASE:
        raise downloader.DownloadError("Style broker must use the configured Aelixhub Red endpoint")
    if not token or len(token) > 4096 or any(char.isspace() or ord(char) < 33 or ord(char) > 126 for char in token):
        raise downloader.DownloadError("Style broker credential is missing or invalid")
    if not ASSET_HOST.fullmatch(host):
        raise downloader.DownloadError("Style asset host must be an exact private Vercel Blob hostname")
    return base, token, host


def expiry_seconds(value):
    if type(value) is int:
        return value / 1000
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                return parsed.timestamp()
        except ValueError:
            pass
    raise downloader.DownloadError("Style broker expiry is invalid")


def validate_response(document, model, host, now=None):
    if not isinstance(document, dict):
        raise downloader.DownloadError("Style broker returned an invalid document")
    digest = document.get("sha256")
    size = document.get("sizeBytes")
    if (not isinstance(digest, str) or digest.lower() != model["sha256"].lower()
            or type(size) is not int or size != model["sizeBytes"]):
        raise downloader.DownloadError("Style broker metadata does not match the pinned model")
    if expiry_seconds(document.get("expiresAt")) <= (time.time() if now is None else now) + 30:
        raise downloader.DownloadError("Style asset link expires too soon")
    url = document.get("url")
    if not isinstance(url, str) or not url or len(url) > 12_000:
        raise downloader.DownloadError("Style asset link is invalid")
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "https" or parsed.netloc != host or parsed.fragment
            or parsed.username or parsed.password or not parsed.path.startswith("/")):
        raise downloader.DownloadError("Style asset link must use the configured private store")
    return url


def request_asset_url(model, base, token, host, opener, limiter):
    request = urllib.request.Request(
        base + "/" + model["id"],
        headers={"Accept": "application/json", "User-Agent": "aelix-style-bootstrap/1.0"},
    )
    request.add_unredirected_header("Authorization", "Bearer " + token)
    try:
        with opener.open(request, timeout=min(20, limiter.remaining())) as response:
            if response.status != 200 or response.headers.get("Content-Type", "").split(";", 1)[0].lower() != "application/json":
                raise downloader.DownloadError("Style broker returned an unexpected response")
            raw = response.read(MAX_BROKER_BYTES + 1)
            if len(raw) > MAX_BROKER_BYTES:
                raise downloader.DownloadError("Style broker response exceeds its size limit")
            document = json.loads(raw)
    except urllib.error.HTTPError as exc:
        raise downloader.DownloadError(f"Style broker returned HTTP {exc.code}") from None
    except (urllib.error.URLError, OSError, ValueError):
        raise downloader.DownloadError("Style broker response could not be read") from None
    return validate_response(document, model, host)


def bootstrap(manifest, output_dir, environ, opener=None):
    models = downloader.load_manifest(manifest)
    if {model["id"] for model in models} != PINNED_IDS or len(models) != 2:
        raise downloader.DownloadError("Unexpected style model selection")
    if any(type(model.get("sizeBytes")) is not int for model in models):
        raise downloader.DownloadError("Style models require exact byte sizes")
    pending = []
    for model in models:
        try:
            downloader.verify_file(output_dir / model["filename"], model)
        except downloader.IntegrityError:
            pending.append(model)
    if not pending:
        print("Aelix styles: both pinned files already verified", flush=True)
        return
    base, token, host = configuration(environ)
    output_dir.mkdir(parents=True, exist_ok=True)
    needed = sum(model["sizeBytes"] for model in pending) + MIN_FREE_BYTES
    if shutil.disk_usage(output_dir).free < needed:
        raise downloader.DownloadError("Insufficient free disk for optional style models")
    opener = opener or urllib.request.build_opener(NoRedirects())
    limiter = downloader.RequestStartLimiter(0, BOOTSTRAP_BUDGET_SECONDS)
    for model in pending:
        url = request_asset_url(model, base, token, host, opener, limiter)
        # Credentials live only in memory. The committed source metadata stays
        # unchanged, and this request contains no broker Authorization header.
        download = dict(model, downloadUrl=url)
        result = downloader.download_one(download, output_dir, retries=2, timeout=20,
                                         opener=opener, limiter=limiter)
        print(f"Aelix styles: {model['id']} {result} and verified", flush=True)
    print("Aelix styles: both pinned files ready", flush=True)


def main(environ=None, exec_start=None):
    environ = os.environ if environ is None else environ
    exec_start = exec_start or os.execv
    try:
        if environ.get("AELIX_STYLE_BOOTSTRAP") == "1":
            bootstrap(Path(__file__).with_name("style-loras.json"), Path("/comfyui/models/loras"), environ)
    except Exception as exc:
        # Optional experiments must not prevent existing character/base jobs.
        # URLs, credentials, upstream bodies and tracebacks must never be logged.
        reason = str(exc) if isinstance(exc, downloader.DownloadError) else type(exc).__name__
        print(f"Aelix styles unavailable: {reason}. Starting the existing worker.", file=sys.stderr, flush=True)
    finally:
        environ.pop("AELIX_STYLE_BROKER_TOKEN", None)
    exec_start("/start.sh", ["/start.sh"])


if __name__ == "__main__":
    main()
