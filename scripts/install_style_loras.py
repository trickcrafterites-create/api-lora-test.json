#!/usr/bin/env python3
"""Install style weights using a mounted Civitai secret and the pinned verifier."""

import argparse
import os
from pathlib import Path
import shutil
import sys
import urllib.parse
import urllib.request

import download_loras as downloader


def read_token(path):
    try:
        with path.open("r", encoding="utf-8") as stream:
            raw = stream.read(4097)
    except (OSError, UnicodeError):
        raise downloader.DownloadError("Cannot read the mounted Civitai token secret") from None
    value = raw.strip()
    if not value or len(raw) > 4096 or any(char.isspace() or ord(char) < 32 or ord(char) > 126 for char in value):
        raise downloader.DownloadError("The mounted Civitai token secret is invalid")
    return value


class CivitaiAuthorization(urllib.request.BaseHandler):
    """Authorize only Civitai downloads; signed CDN redirects never receive it."""

    def __init__(self, token):
        self.token = token

    def https_request(self, request):
        parsed = urllib.parse.urlsplit(request.full_url)
        if (parsed.scheme == "https" and parsed.netloc == "civitai.com"
                and parsed.path.startswith("/api/download/models/")):
            request.add_unredirected_header("Authorization", "Bearer " + self.token)
        return request


def install(models, output_dir, token):
    if any(model.get("requiresAuthentication") for model in models) and not token:
        raise downloader.DownloadError("These Civitai downloads require a mounted token secret; no files were downloaded")
    output_dir.mkdir(parents=True, exist_ok=True)
    handlers = [downloader.HTTPSRedirectHandler()]
    if token:
        handlers.append(CivitaiAuthorization(token))
    opener = urllib.request.build_opener(*handlers)
    limiter = downloader.RequestStartLimiter(min_interval=6, max_elapsed=1200)
    for index, model in enumerate(models, 1):
        result = downloader.download_one(model, output_dir, opener=opener, limiter=limiter)
        print(f"[{index}/{len(models)}] {model['id']}: {result}", flush=True)
    print(f"Verified and installed all {len(models)} style models.", flush=True)


def install_local(models, source_dir, output_dir):
    # Verify the complete selection before modifying the output directory.
    for model in models:
        downloader.verify_file(source_dir / model["filename"], model)
    output_dir.mkdir(parents=True, exist_ok=True)
    for index, model in enumerate(models, 1):
        source = source_dir / model["filename"]
        destination = output_dir / model["filename"]
        partial = output_dir / f"{model['filename']}.{model['sha256'][:12].lower()}.part"
        if destination.is_symlink() or partial.is_symlink():
            raise downloader.DownloadError("Refusing symlink destination")
        if source.resolve() != destination.resolve():
            shutil.copyfile(source, partial)
            try:
                downloader.verify_file(partial, model)
            except downloader.IntegrityError:
                partial.unlink(missing_ok=True)
                raise
            os.replace(partial, destination)
        print(f"[{index}/{len(models)}] {model['id']}: verified local file", flush=True)
    print(f"Verified and installed all {len(models)} local style models.", flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--token-file", type=Path)
    source.add_argument("--source-dir", type=Path,
                        help="Use already-owned exact local files; no network or credentials")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args(argv)
    if not args.validate_only and not args.output_dir:
        parser.error("--output-dir is required unless --validate-only is supplied")
    try:
        models = downloader.load_manifest(args.manifest)
        if args.validate_only:
            print(f"Valid style manifest: {len(models)} pinned Illustrious models")
            return 0
        if args.source_dir:
            install_local(models, args.source_dir, args.output_dir)
        else:
            token = read_token(args.token_file) if args.token_file else None
            install(models, args.output_dir, token)
        return 0
    except (downloader.DownloadError, OSError, ValueError) as exc:
        # OS/parser errors can contain paths or upstream data. Only the existing
        # verifier's deliberately sanitized errors are appropriate in build logs.
        reason = str(exc) if isinstance(exc, downloader.DownloadError) else type(exc).__name__
        print(f"Style installation failed: {reason}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
