"""Immutable, local-only science-QA run receipts.

Each execution has a distinct directory and a manifest written *last*.
Original FITS are hashed directly; their integrity is rechecked after the
science operation. Failed runs cannot silently masquerade as complete.
These are provenance controls, NOT remote-authoritative checksums.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import uuid

from importlib.metadata import PackageNotFoundError, version

from lsbpipe.surveys.legacy import SkyField, BrickHit, product_for


def atomic_json(path: Path, data: dict) -> Path:
    """Atomically publish UTF-8 metadata; never replace a science run."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temp.open("x", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    return path


def _checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def original_fits_inventory(data_root: Path, hit: BrickHit, band: str) -> dict:
    """Ground one original quartet by path, size, mtime, and local SHA-256."""
    result = {}
    for kind in ("image", "invvar", "nexp", "maskbits"):
        product = product_for(
            hit, kind, None if kind == "maskbits" else band)
        file = Path(data_root) / product.relative_path
        if not file.is_file():
            raise FileNotFoundError(f"Missing {kind} original FITS: {file}")
        before = file.stat()
        digest = _checksum(file)
        after = file.stat()
        if (before.st_size, before.st_mtime_ns) != (
                after.st_size, after.st_mtime_ns):
            raise RuntimeError(f"Original FITS changed while hashing: {file}")
        result[kind] = {
            "path": str(file.resolve()),
            "relative_path": product.relative_path,
            "size_bytes": after.st_size,
            "mtime_ns": after.st_mtime_ns,
            "local_sha256": digest,
        }
    return result


def assert_source_unchanged(inventory: dict) -> None:
    """Reject changing inputs even when their path and shape remain the same."""
    for region, files in inventory.items():
        for kind, recorded in files.items():
            path = Path(recorded["path"])
            state = path.stat()
            if (state.st_size != recorded["size_bytes"] or
                    state.st_mtime_ns != recorded["mtime_ns"] or
                    _checksum(path) != recorded["local_sha256"]):
                raise RuntimeError(
                    f"Original {region} {kind} FITS modified during analysis")


def begin_run(data_root: Path, *, action: str) -> tuple[str, Path, str]:
    """Create a unique run directory outside the source data/ subtree."""
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    run_id += "_" + uuid.uuid4().hex[:12]
    result_root = Path(data_root).resolve().parent / "lsbpipe-qa-reports"
    folder = result_root / run_id
    folder.mkdir(parents=True, exist_ok=False)
    return run_id, folder, datetime.now(timezone.utc).isoformat()


def publish_run(
    folder: Path, *, run_id: str, created_utc: str,
    action: str, field: SkyField, hit: BrickHit, band: str,
    radii: tuple[float, float, float], inventory: dict,
    result_path: Path, status: str = "DIAGNOSTIC_NOT_ADOPTED",
) -> Path:
    """Finalise complete run only when all original FITS remain identical."""
    assert_source_unchanged(inventory)
    result_path = Path(result_path).resolve()
    if not result_path.is_file() or result_path.parent != Path(folder).resolve():
        raise ValueError("Final artifact must exist inside the unique run folder")
    try:
        pkg_version = version("lsbpipe-prototype")
    except PackageNotFoundError:
        pkg_version = "unknown"
    manifest = {
        "schema": "lsbpipe.science-qa-run/1",
        "run_id": run_id, "created_utc": created_utc,
        "action": action, "status": status,
        "software": {"name": "LSBpipe", "version": pkg_version},
        "selected_sky": {
            "frame": "ICRS", "ra_deg": field.ra_deg,
            "dec_deg": field.dec_deg,
            "square_side_arcmin": field.width_arcmin,
            "target_radius_arcsec": radii[0],
            "sky_inner_arcsec": radii[1],
            "sky_outer_arcsec": radii[2],
        },
        "selected_product": {
            "release": hit.release, "region": hit.region,
            "brick": hit.name, "band": band,
        },
        "input_fits": inventory,
        "result": {
            "file": result_path.name,
            "size_bytes": result_path.stat().st_size,
            "local_sha256": _checksum(result_path),
        },
        "integrity_note": (
            "SHA256 values are locally computed content fingerprints, "
            "not authoritative remote archive checksums."
        ),
        "science_note": (
            "Diagnostic inspection only: no sky, PSF or LSB photometric "
            "adoption; North and South are not independent exposures."
        ),
    }
    return atomic_json(Path(folder) / "manifest.json", manifest)
