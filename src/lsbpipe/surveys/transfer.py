"""Conservative HTTP verification and atomic downloads of Legacy archive products."""
from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Callable
import hashlib
import json
import os
import urllib.error
import urllib.request

from .legacy import ARCHIVE


def _validated_product(entry: dict) -> tuple[str, Path]:
    raw = entry.get("relative_path")
    url = entry.get("url")
    if not isinstance(raw, str) or not isinstance(url, str):
        raise ValueError("Malformed download entry")
    path = PurePosixPath(raw)
    if (path.is_absolute() or ".." in path.parts or len(path.parts) < 5 or
            any(p in ("", ".") for p in path.parts)):
        raise ValueError("Unsafe relative path")
    if path.parts[0] not in ("dr9", "dr10", "dr11"):
        raise ValueError("Invalid release directory")
    if url != ARCHIVE + "/" + raw:
        raise ValueError("Download URL must match official Legacy archive path")
    if not raw.endswith((".fits", ".fits.fz", ".fits.gz")):
        raise ValueError("Unexpected science file extension")
    return url, Path(*path.parts)


def probe_product(entry: dict, *, timeout: float = 20) -> dict:
    """HTTP HEAD is evidence of presence, not proof of scientific coverage."""
    url, _ = _validated_product(entry)
    req = urllib.request.Request(
        url, method="HEAD", headers={"User-Agent": "LSBpipe-survey/0.4"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            size = response.headers.get("Content-Length")
            return {"evidence": "HTTP_OK", "http_status": response.status,
                    "size_bytes": int(size) if size and size.isdigit() else None}
    except urllib.error.HTTPError as exc:
        if exc.code in (404, 410):
            return {"evidence": "HTTP_MISSING", "http_status": exc.code,
                    "size_bytes": None}
        return {"evidence": "HTTP_UNVERIFIED", "http_status": exc.code,
                "size_bytes": None}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"evidence": "HTTP_ERROR", "error": str(exc),
                "size_bytes": None}


def _hash_file(path: Path) -> str:
    dig = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            dig.update(chunk)
    return dig.hexdigest()


def download_product(entry: dict, destination_root: Path, *,
                     max_bytes: int = 2_000_000_000,
                     progress: Callable[[int], None] | None = None) -> dict:
    """Download to a temporary file, validate length/FITS signature, then rename.

    sha256 is calculated from the downloaded bytes, NOT compared to an
    authoritative checksum. Existing files are reused but *not* asserted
    identical to the remote source.
    """
    url, relative = _validated_product(entry)
    root = Path(destination_root)
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file():
        if target.stat().st_size == 0:
            raise ValueError(f"Existing file is empty: {target}")
        return {"relative_path":relative.as_posix(), "state":"REUSED_LOCAL_UNVERIFIED",
                "size_bytes":target.stat().st_size,"sha256":_hash_file(target)}
    part = target.with_name(target.name + ".part")
    req = urllib.request.Request(url, headers={"User-Agent":"LSBpipe-survey/0.4"})
    try:
        with urllib.request.urlopen(req, timeout=90) as response:
            if getattr(response, "status", 200) != 200:
                raise IOError("Unexpected HTTP status")
            length = response.headers.get("Content-Length")
            expected = int(length) if length and length.isdigit() else None
            if expected is not None and expected > max_bytes:
                raise ValueError("Remote file larger than per-file limit")
            digest = hashlib.sha256()
            n = 0
            first = b""
            with part.open("wb") as f:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    if n == 0:
                        first = chunk[:80]
                    n += len(chunk)
                    if n > max_bytes:
                        raise ValueError("File exceeded per-file limit")
                    f.write(chunk)
                    digest.update(chunk)
                    if progress:
                        progress(len(chunk))
            if not first.startswith(b"SIMPLE  ="):
                raise IOError("Downloaded content is not a FITS primary HDU")
            if expected is not None and n != expected:
                raise IOError("Incomplete download: HTTP Content-Length mismatch")
            os.replace(part, target)
            return {"relative_path":relative.as_posix(),"state":"DOWNLOADED",
                    "size_bytes":n,"sha256":digest.hexdigest()}
    finally:
        part.unlink(missing_ok=True)


def download_manifest(manifest: dict, destination_root: Path, *,
                      product_paths: set[str] | None = None,
                      progress: Callable[[int, int, str], None] | None = None,
                      continue_on_error: bool = True) -> dict:
    if manifest.get("schema") != "lsbpipe.legacy-download/0.1":
        raise ValueError("Unknown download manifest schema")
    entries = [
        entry for entry in manifest["products"]
        if product_paths is None or entry["relative_path"] in product_paths
    ]
    if product_paths is not None and len(entries) != len(product_paths):
        raise ValueError("Selected products not present in manifest")
    if len(entries) > 300:
        raise ValueError("More than 300 files requested; narrow selection")
    root = Path(destination_root)
    results = []
    for i, entry in enumerate(entries, 1):
        try:
            def bytes_progress(_n):
                if progress:
                    progress(i-1, len(entries), entry["relative_path"])
            result = download_product(entry, root, progress=bytes_progress)
            results.append(result)
        except (urllib.error.URLError, OSError, ValueError, IOError) as exc:
            results.append({"relative_path":entry.get("relative_path"),
                            "state":"ERROR","error":str(exc)})
            if not continue_on_error:
                raise
        if progress:
            progress(i, len(entries), entry["relative_path"])
    report = {
        "schema":"lsbpipe.legacy-download-report/0.1",
        "field":manifest["field"],"results":results,
        "complete":all(x["state"] != "ERROR" for x in results),
        "integrity_note":"SHA256 is a local content fingerprint; remote authoritative checksums were not supplied.",
    }
    root.mkdir(parents=True, exist_ok=True)
    (root / "download-report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report
