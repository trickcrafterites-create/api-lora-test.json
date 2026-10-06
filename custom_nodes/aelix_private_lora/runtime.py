"""Materialize one pinned private Vercel Blob object for ComfyUI.

This module deliberately has no ComfyUI imports so its URL, integrity, locking,
and cache behavior can be tested with the Python standard library alone.
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import re
import stat
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


CHUNK_SIZE = 1024 * 1024
MAX_BLOB_BYTES = 4 * 1024**3
MAX_HEADER_BYTES = 100 * 1024**2
MAX_SIGNED_URL_CHARS = 64 * 1024
PERSISTENT_MOUNT = Path("/runpod-volume")
PERSISTENT_CACHE = PERSISTENT_MOUNT / "aelix-anima" / "loras"
FALLBACK_CACHE = Path("/comfyui/models/loras/aelix-private-cache")

_BLOB_HOST = re.compile(
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.private\.blob\.vercel-storage\.com\Z",
    re.ASCII,
)
_SHA256 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_REQUIRED_SIGNED_QUERY = ("vercel-blob-delegation", "vercel-blob-signature")
_PROCESS_LOCKS: dict[str, threading.Lock] = {}
_PROCESS_LOCKS_GUARD = threading.Lock()


class PrivateLoraError(RuntimeError):
    """A URL-free error that is safe to surface in worker logs."""


class IntegrityError(PrivateLoraError):
    """The downloaded or cached bytes do not match the pinned object."""


def _reject_controls(value: str, label: str) -> None:
    if any(ord(character) <= 0x20 or ord(character) == 0x7F for character in value):
        raise PrivateLoraError(f"{label} contains whitespace or control characters")


def validate_signed_blob_url(value: object) -> str:
    """Accept only one directly fetchable, signed private Vercel Blob URL."""
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_SIGNED_URL_CHARS
        or not value.isascii()
    ):
        raise PrivateLoraError("signed_url must be a non-empty private Blob URL")
    _reject_controls(value, "signed_url")
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise PrivateLoraError("signed_url is malformed") from exc
    hostname = parsed.hostname
    if parsed.scheme.lower() != "https" or not hostname or not _BLOB_HOST.fullmatch(hostname.lower()):
        raise PrivateLoraError("signed_url must use the approved private Vercel Blob HTTPS host")
    if parsed.username is not None or parsed.password is not None or port is not None:
        raise PrivateLoraError("signed_url must not contain credentials or a port")
    if parsed.fragment:
        raise PrivateLoraError("signed_url must not contain a fragment")
    if not parsed.path or parsed.path == "/" or "\\" in parsed.path:
        raise PrivateLoraError("signed_url must identify one Blob object")
    try:
        pairs = urllib.parse.parse_qsl(
            parsed.query,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=32,
        )
    except (ValueError, OverflowError) as exc:
        raise PrivateLoraError("signed_url has an invalid query") from exc
    counts: dict[str, int] = {}
    for key, query_value in pairs:
        _reject_controls(key, "signed_url query key")
        _reject_controls(query_value, "signed_url query value")
        if len(key) > 128 or len(query_value) > 48 * 1024:
            raise PrivateLoraError("signed_url query is too large")
        counts[key] = counts.get(key, 0) + 1
    if any(count > 1 for count in counts.values()):
        raise PrivateLoraError("signed_url must not contain duplicate query parameters")
    for required in _REQUIRED_SIGNED_QUERY:
        values = [query_value for key, query_value in pairs if key == required]
        if len(values) != 1 or not values[0]:
            raise PrivateLoraError("signed_url is missing required Vercel Blob signing data")
    return value


def validate_pin(sha256: object, size_bytes: object) -> tuple[str, int]:
    if not isinstance(sha256, str) or not _SHA256.fullmatch(sha256.lower()):
        raise PrivateLoraError("sha256 must be exactly 64 hexadecimal characters")
    if type(size_bytes) is not int or not 0 < size_bytes <= MAX_BLOB_BYTES:
        raise PrivateLoraError("size_bytes must be an exact positive integer no larger than 4 GiB")
    return sha256.lower(), size_bytes


def _decode_mount_field(value: str) -> str:
    for encoded, decoded in ((r"\040", " "), (r"\011", "\t"), (r"\012", "\n"), (r"\134", "\\")):
        value = value.replace(encoded, decoded)
    return value


def is_actual_mount(path: Path) -> bool:
    """Recognize ordinary and bind mounts without treating a baked directory as persistent."""
    try:
        if os.path.ismount(path):
            return True
        target = str(path.resolve())
        with open("/proc/self/mountinfo", "r", encoding="utf-8") as mountinfo:
            for line in mountinfo:
                fields = line.split(" - ", 1)[0].split()
                if len(fields) > 4 and _decode_mount_field(fields[4]) == target:
                    return True
    except (OSError, RuntimeError):
        pass
    return False


def _ensure_cache_directory(path: Path) -> None:
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = path.lstat()
    except OSError as exc:
        raise PrivateLoraError("private LoRA cache directory is unavailable") from exc
    if path.is_symlink() or not stat.S_ISDIR(info.st_mode):
        raise PrivateLoraError("private LoRA cache path must be a real directory")
    if os.name != "nt":
        try:
            path.chmod(0o700)
        except OSError as exc:
            raise PrivateLoraError("private LoRA cache permissions could not be secured") from exc


def _probe_writable(path: Path) -> None:
    probe = path / f".write-probe-{os.getpid()}-{uuid.uuid4().hex}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_BINARY", 0)
    descriptor = None
    try:
        descriptor = os.open(probe, flags, 0o600)
        os.write(descriptor, b"aelix")
        os.fsync(descriptor)
    except OSError as exc:
        raise PrivateLoraError("private LoRA cache is not writable") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            probe.unlink(missing_ok=True)
        except OSError:
            pass


def select_cache_directory(
    *,
    persistent_mount: Path = PERSISTENT_MOUNT,
    persistent_cache: Path = PERSISTENT_CACHE,
    fallback_cache: Path = FALLBACK_CACHE,
    mount_checker=None,
) -> Path:
    """Use the network volume only when its root is an actual writable mount."""
    mount_checker = mount_checker or is_actual_mount
    persistent_mount = Path(persistent_mount)
    persistent_cache = Path(persistent_cache)
    fallback_cache = Path(fallback_cache)
    try:
        mounted = (
            persistent_mount.exists()
            and not persistent_mount.is_symlink()
            and persistent_mount.is_dir()
            and bool(mount_checker(persistent_mount))
        )
    except OSError:
        mounted = False
    if mounted:
        try:
            _ensure_cache_directory(persistent_cache)
            _probe_writable(persistent_cache)
            return persistent_cache
        except PrivateLoraError:
            # A mounted but read-only volume is not a usable cache. The image-local
            # cache remains isolated and avoids falsely claiming persistence.
            pass
    _ensure_cache_directory(fallback_cache)
    _probe_writable(fallback_cache)
    return fallback_cache


def _no_duplicate_json_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def verify_safetensors_file(
    path: Path,
    expected_size: int,
    expected_sha256: str | None = None,
) -> None:
    """Verify the regular file, exact size, SafeTensors layout, and optional digest."""
    path = Path(path)
    normalized_sha = None
    if expected_sha256 is not None:
        normalized_sha, expected_size = validate_pin(expected_sha256, expected_size)
    else:
        _, expected_size = validate_pin("0" * 64, expected_size)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise IntegrityError("cached file is missing, inaccessible, or a symlink") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise IntegrityError("cached object is not a regular file")
        if info.st_size != expected_size:
            raise IntegrityError("private LoRA byte size does not match the pin")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            prefix = stream.read(8)
            if len(prefix) != 8:
                raise IntegrityError("private LoRA is truncated")
            header_size = struct.unpack("<Q", prefix)[0]
            if not 2 <= header_size <= MAX_HEADER_BYTES or header_size + 8 >= expected_size:
                raise IntegrityError("private LoRA has an invalid SafeTensors header length")
            header_bytes = stream.read(header_size)
            if len(header_bytes) != header_size:
                raise IntegrityError("private LoRA SafeTensors header is truncated")
            try:
                header = json.loads(
                    header_bytes.decode("utf-8"),
                    object_pairs_hook=_no_duplicate_json_keys,
                )
            except (UnicodeDecodeError, ValueError, RecursionError) as exc:
                raise IntegrityError("private LoRA has an invalid SafeTensors JSON header") from exc
            if not isinstance(header, dict):
                raise IntegrityError("private LoRA SafeTensors header must be an object")
            data_size = expected_size - 8 - header_size
            ranges: list[tuple[int, int]] = []
            tensor_count = 0
            for name, tensor in header.items():
                if name == "__metadata__":
                    if not isinstance(tensor, dict):
                        raise IntegrityError("private LoRA has invalid SafeTensors metadata")
                    continue
                tensor_count += 1
                if not isinstance(name, str) or not name or not isinstance(tensor, dict):
                    raise IntegrityError("private LoRA has an invalid SafeTensors tensor entry")
                dtype = tensor.get("dtype")
                shape = tensor.get("shape")
                offsets = tensor.get("data_offsets")
                if not isinstance(dtype, str) or not dtype or len(dtype) > 32:
                    raise IntegrityError("private LoRA has an invalid SafeTensors dtype")
                if not isinstance(shape, list) or any(type(item) is not int or item < 0 for item in shape):
                    raise IntegrityError("private LoRA has an invalid SafeTensors shape")
                if (
                    not isinstance(offsets, list)
                    or len(offsets) != 2
                    or any(type(item) is not int for item in offsets)
                ):
                    raise IntegrityError("private LoRA has invalid SafeTensors offsets")
                start, end = offsets
                if not 0 <= start <= end <= data_size:
                    raise IntegrityError("private LoRA SafeTensors offsets exceed the data buffer")
                ranges.append((start, end))
            if tensor_count == 0:
                raise IntegrityError("private LoRA contains no tensors")
            cursor = 0
            for start, end in sorted(ranges):
                if start != cursor:
                    raise IntegrityError("private LoRA SafeTensors ranges overlap or contain gaps")
                cursor = end
            if cursor != data_size:
                raise IntegrityError("private LoRA SafeTensors data buffer is not fully described")
            if normalized_sha is not None:
                stream.seek(0)
                digest = hashlib.sha256()
                for chunk in iter(lambda: stream.read(CHUNK_SIZE), b""):
                    digest.update(chunk)
                if digest.hexdigest() != normalized_sha:
                    raise IntegrityError("private LoRA SHA-256 does not match the pin")
    finally:
        os.close(descriptor)


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Never allow a signed capability to be redirected to another destination."""

    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        raise PrivateLoraError("private Blob redirects are not allowed")


def _default_opener():
    # Ignore ambient proxy variables: this worker only needs a direct TLS request
    # to the single allowlisted Blob object host.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirectHandler())


def _get_process_lock(key: str) -> threading.Lock:
    with _PROCESS_LOCKS_GUARD:
        return _PROCESS_LOCKS.setdefault(key, threading.Lock())


@contextlib.contextmanager
def _file_lock(path: Path):
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise PrivateLoraError("private LoRA cache lock could not be opened") from exc
    locked = False
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise PrivateLoraError("private LoRA cache lock is not a regular file")
        if os.name == "nt":
            import msvcrt

            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
            while True:
                try:
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                    locked = True
                    break
                except OSError as exc:
                    if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                        raise
                    time.sleep(0.05)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX)
            locked = True
        yield
    finally:
        if locked:
            if os.name == "nt":
                import msvcrt

                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _remove_corrupt_cache_file(path: Path) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if path.is_symlink() or not stat.S_ISREG(info.st_mode):
        raise PrivateLoraError("refusing an unsafe private LoRA cache entry")
    try:
        path.unlink()
    except OSError as exc:
        raise PrivateLoraError("corrupt private LoRA cache entry could not be replaced") from exc


def _fsync_directory(path: Path) -> None:
    if os.name == "nt" or not hasattr(os, "O_DIRECTORY"):
        return
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _download(
    signed_url: str,
    destination: Path,
    expected_sha256: str,
    expected_size: int,
    *,
    opener,
    timeout: float,
) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_BINARY", 0)
    descriptor = None
    try:
        descriptor = os.open(destination, flags, 0o600)
        request = urllib.request.Request(
            signed_url,
            method="GET",
            headers={
                "Accept": "application/octet-stream",
                "Accept-Encoding": "identity",
                "User-Agent": "aelix-private-lora-loader/1.0",
            },
        )
        try:
            response_context = opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            raise PrivateLoraError(f"private Blob download failed with HTTP {exc.code}") from None
        except PrivateLoraError:
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            raise PrivateLoraError(f"private Blob download failed ({type(exc).__name__})") from None
        try:
            with response_context as response:
                status_code = getattr(response, "status", None)
                if status_code != 200:
                    raise PrivateLoraError("private Blob download returned an unexpected HTTP status")
                content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                if (
                    content_type.startswith("text/")
                    or content_type in {
                        "application/json",
                        "application/problem+json",
                        "application/xml",
                        "application/xhtml+xml",
                        "application/javascript",
                    }
                ):
                    raise IntegrityError("private Blob returned an error document instead of model weights")
                content_encoding = response.headers.get("Content-Encoding", "").strip().lower()
                if content_encoding not in ("", "identity"):
                    raise IntegrityError("private Blob response must not transform the pinned bytes")
                content_length = response.headers.get("Content-Length")
                if content_length is not None:
                    try:
                        if int(content_length) != expected_size:
                            raise IntegrityError("private Blob Content-Length does not match the pin")
                    except (TypeError, ValueError) as exc:
                        raise IntegrityError("private Blob returned an invalid Content-Length") from exc
                received = 0
                digest = hashlib.sha256()
                with os.fdopen(descriptor, "wb", closefd=False) as output:
                    while True:
                        chunk = response.read(CHUNK_SIZE)
                        if not chunk:
                            break
                        if not isinstance(chunk, (bytes, bytearray)):
                            raise IntegrityError("private Blob returned an invalid byte stream")
                        received += len(chunk)
                        if received > expected_size or received > MAX_BLOB_BYTES:
                            raise IntegrityError("private Blob download exceeded its pinned byte size")
                        output.write(chunk)
                        digest.update(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                if received != expected_size:
                    raise IntegrityError("private Blob download ended before the pinned byte size")
                if digest.hexdigest() != expected_sha256:
                    raise IntegrityError("private LoRA SHA-256 does not match the pin")
        except PrivateLoraError:
            raise
        except (
            urllib.error.URLError,
            TimeoutError,
            ConnectionError,
            OSError,
            http.client.HTTPException,
        ) as exc:
            raise PrivateLoraError(f"private Blob download failed ({type(exc).__name__})") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def materialize_signed_lora(
    signed_url: object,
    sha256: object,
    size_bytes: object,
    *,
    cache_dir: Path | None = None,
    opener=None,
    timeout: float = 300,
) -> Path:
    """Return a verified cache path for exactly one signed and pinned Blob object."""
    validated_url = validate_signed_blob_url(signed_url)
    normalized_sha, expected_size = validate_pin(sha256, size_bytes)
    if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise PrivateLoraError("download timeout must be finite and positive")
    if cache_dir is None:
        cache_root = select_cache_directory()
    else:
        cache_root = Path(cache_dir)
        _ensure_cache_directory(cache_root)
    destination = cache_root / f"{normalized_sha}.safetensors"
    lock_path = cache_root / f".{normalized_sha}.lock"
    process_lock = _get_process_lock(normalized_sha)
    with process_lock, _file_lock(lock_path):
        if destination.exists() or destination.is_symlink():
            try:
                verify_safetensors_file(destination, expected_size, normalized_sha)
                return destination
            except IntegrityError:
                _remove_corrupt_cache_file(destination)
        partial = cache_root / (
            f".{normalized_sha}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.part"
        )
        try:
            _download(
                validated_url,
                partial,
                normalized_sha,
                expected_size,
                opener=opener or _default_opener(),
                timeout=float(timeout),
            )
            verify_safetensors_file(partial, expected_size)
            os.replace(partial, destination)
            _fsync_directory(cache_root)
            try:
                verify_safetensors_file(destination, expected_size, normalized_sha)
            except IntegrityError:
                _remove_corrupt_cache_file(destination)
                raise
            return destination
        except PrivateLoraError:
            raise
        except OSError as exc:
            raise PrivateLoraError("private LoRA could not be installed in the isolated cache") from exc
        finally:
            try:
                partial.unlink(missing_ok=True)
            except OSError:
                pass
