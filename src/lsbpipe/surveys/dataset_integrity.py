"""P0.2 offline Legacy FITS dataset registry and sampled multi-brick footprint.

Original files are only opened for reading. This diagnostic NEVER makes a
photometric-quality decision, merges coadds, reprojects images, or treats
North/South reductions as independent exposures.
"""
from __future__ import annotations

from collections import defaultdict
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import secrets

import numpy as np

from lsbpipe.survey import LEGACY_BITS, _check_wcs_compatible, maskbits_to_uint
from .coverage import _science_hdu_index
from .legacy import BrickHit, SkyField, product_for, regions_for_release
from .local_project import _atomic_json

SCHEMA = "lsbpipe.dataset-audit/1"
KINDS = ("image", "invvar", "nexp", "maskbits")
BRICK = re.compile(r"[0-9]{4}[pm][0-9]{3}")
BAND_PRODUCT = re.compile(
    r"legacysurvey-([0-9]{4}[pm][0-9]{3})-(image|invvar|nexp)-([griz])\.fits\.fz")
MASK_PRODUCT = re.compile(
    r"legacysurvey-([0-9]{4}[pm][0-9]{3})-maskbits\.fits\.fz")
MAX_DATASETS = 64
MAX_GRID_CELLS = 160_000


def _expected_paths(data_root: Path, key: tuple[str, str, str, str]) -> dict:
    release, region, brick, band = key
    hit = BrickHit(release, region, brick, 0, 0, 0, 0, {band: None})
    return {
        kind: data_root / product_for(
            hit, kind, None if kind == "maskbits" else band).relative_path
        for kind in KINDS
    }


def discover_dataset_inventory(data_root: str | Path) -> list[dict]:
    """Inventory every available science product, including orphan INVVAR/NEXP.

    Does not rely on an IMAGE being present and does not infer availability
    from a brick summary or the internet. MASKBITS without a band cannot by
    itself identify a band-specific quartet.
    """
    root = Path(data_root).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Original FITS data root not found: {root}")
    found: dict[tuple[str, str, str, str], set[str]] = defaultdict(set)
    masks: dict[tuple[str, str, str], Path] = {}
    for release in ("dr9", "dr10", "dr11"):
        for region in regions_for_release(release):
            coadd = root / release / region / "coadd"
            if not coadd.is_dir():
                continue
            for file in coadd.glob("*/*/legacysurvey-*.fits.fz"):
                if file.is_symlink():
                    continue  # Never follow an untrusted external symlink.
                name = file.name
                match = BAND_PRODUCT.fullmatch(name)
                mask = MASK_PRODUCT.fullmatch(name)
                if not match and not mask:
                    continue
                brick = (match or mask).group(1)
                if file.parent.name != brick or file.parent.parent.name != brick[:3]:
                    continue
                if match:
                    _, kind, band = match.groups()
                    if region == "north" and band == "i":
                        continue
                    found[(release, region, brick, band)].add(kind)
                else:
                    masks[(release, region, brick)] = file
    # A single MASKBITS file is not a quartet; report it as an orphan
    # separately rather than inventing a band or assigning a fake exposure.
    rows = []
    for key in sorted(found):
        paths = _expected_paths(root, key)
        products = {}
        for kind, file in paths.items():
            try:
                st = file.stat() if file.is_file() and not file.is_symlink() else None
            except OSError:
                st = None
            products[kind] = {
                "relative_path": file.relative_to(root).as_posix(),
                "presence": ("MISSING" if st is None else
                             "EMPTY_FILE" if st.st_size == 0 else "PRESENT_UNVERIFIED"),
                "size_bytes": st.st_size if st is not None else None,
            }
        presences = [x["presence"] for x in products.values()]
        status = ("EMPTY_FILE" if "EMPTY_FILE" in presences else
                  "INCOMPLETE_FILES" if "MISSING" in presences else
                  "PRESENT_UNVERIFIED")
        rows.append({
            "release": key[0], "region": key[1], "brick": key[2],
            "band": key[3], "dataset_id": "/".join(key),
            "status": status, "products": products,
        })
    if len(rows) > MAX_DATASETS:
        raise ValueError(f"Dataset inventory exceeds {MAX_DATASETS} datasets")
    known = {(x["release"], x["region"], x["brick"]) for x in rows}
    for (release, region, brick), path in sorted(masks.items()):
        if (release, region, brick) not in known:
            rows.append({
                "release": release, "region": region, "brick": brick,
                "band": None, "dataset_id": f"{release}/{region}/{brick}/NO_BAND",
                "status": "ORPHAN_MASKBITS",
                "products": {"maskbits": {
                    "relative_path": path.relative_to(root).as_posix(),
                    "presence": "PRESENT_UNVERIFIED",
                    "size_bytes": path.stat().st_size}},
            })
    return rows


def _sha256_file(path: Path) -> dict:
    before = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (
            after.st_size, after.st_mtime_ns):
        raise RuntimeError(f"Source file changed during verification: {path}")
    return {"sha256_local": digest.hexdigest(), "size_bytes": after.st_size,
            "mtime_ns": after.st_mtime_ns,
            "hash_authority": "LOCAL_ONLY_NOT_REMOTE_CHECKSUM"}


def _validate_open_hdus(hdus: dict, band: str):
    """Shared strict scientific 2D raster/WCS contract; no FITS modifications."""
    from astropy.wcs import WCS
    indices = {kind: _science_hdu_index(hdus[kind], kind) for kind in KINDS}
    arrays, systems, units = {}, {}, {}
    for kind in KINDS:
        hdu = hdus[kind][indices[kind]]
        hdu.verify("exception")
        if "CHECKSUM" in hdu.header and hdu.verify_checksum() != 1:
            raise ValueError(f"{kind}: FITS CHECKSUM verification failed")
        if "DATASUM" in hdu.header and hdu.verify_datasum() != 1:
            raise ValueError(f"{kind}: FITS DATASUM verification failed")
        array = hdu.data
        if array is None or array.ndim != 2 or not array.size:
            raise ValueError(f"{kind}: missing nonempty 2D science array")
        if array.dtype.kind not in "iuf":
            raise ValueError(f"{kind}: nonnumeric FITS array")
        arrays[kind] = array
        systems[kind] = WCS(hdu.header).celestial
        units[kind] = hdu.header.get("BUNIT")
        if not systems[kind].has_celestial or systems[kind].pixel_n_dim != 2:
            raise ValueError(f"{kind}: missing two-dimensional celestial WCS")
    if arrays["maskbits"].dtype.kind not in "iu" or arrays["maskbits"].dtype.itemsize not in (2, 4):
        raise ValueError("MASKBITS: expected 16/32-bit integer raster")
    shape = arrays["image"].shape
    for kind in ("invvar", "nexp", "maskbits"):
        if arrays[kind].shape != shape:
            raise ValueError(f"{kind}: raster shape mismatch")
        _check_wcs_compatible(systems["image"], systems[kind], shape, kind)
    img_unit = str(units["image"] or "").lower().replace(" ", "")
    if img_unit and img_unit not in (
            "nanomaggy", "nanomaggies", "nanomaggies/pixel",
            "nanomaggy/pixel", "nanomaggy/pix", "nanomaggies/pix"):
        raise ValueError(f"IMAGE BUNIT is not expected nanomaggies: {units['image']}")
    return arrays, systems["image"], indices, units


def verify_dataset(data_root: str | Path, entry: dict, *,
                   hash_inputs: bool = True) -> dict:
    """Read all four FITS, verify HDU/WCS/units and conservative numeric gates.

    A validated quartet is still DIAGNOSTIC_NOT_ADOPTED. It is not a claim of
    background quality, PSF adequacy or photometric readiness.
    """
    from astropy.io import fits
    root = Path(data_root).expanduser().resolve()
    result = dict(entry)
    if entry["band"] is None:
        return result
    if entry["status"] != "PRESENT_UNVERIFIED":
        return result
    key = (entry["release"], entry["region"], entry["brick"], entry["band"])
    paths = _expected_paths(root, key)
    try:
        with ExitStack() as stack:
            hdus = {kind: stack.enter_context(
                fits.open(file, memmap=True, do_not_scale_image_data=False))
                for kind, file in paths.items()}
            arrays, wcs, indices, units = _validate_open_hdus(
                hdus, entry["band"])
            image, ivar, nexp, mask = (arrays[k] for k in KINDS)
            total = int(image.size)
            numerically_valid = 0
            positive_ivar = positive_nexp = finite_image = saturated = 0
            for start in range(0, image.shape[0], 256):
                stop = min(start + 256, image.shape[0])
                finite = np.isfinite(image[start:stop])
                ivgood = np.isfinite(ivar[start:stop]) & (ivar[start:stop] > 0)
                ngood = np.isfinite(nexp[start:stop]) & (nexp[start:stop] > 0)
                bits = maskbits_to_uint(mask[start:stop])
                flagged = (bits & np.uint32(
                    (1 << LEGACY_BITS[f"SATUR_{entry['band'].upper()}"]) |
                    (1 << LEGACY_BITS[f"ALLMASK_{entry['band'].upper()}"]))) != 0
                finite_image += int(np.count_nonzero(finite))
                positive_ivar += int(np.count_nonzero(ivgood))
                positive_nexp += int(np.count_nonzero(ngood))
                saturated += int(np.count_nonzero(flagged))
                numerically_valid += int(np.count_nonzero(
                    finite & ivgood & ngood & ~flagged))
            product_info = {}
            for kind, file in paths.items():
                info = dict(entry["products"][kind])
                info["hdu_index"] = int(indices[kind])
                info["bunit"] = units[kind]
                if hash_inputs:
                    info.update(_sha256_file(file))
                product_info[kind] = info
            result.update({
                "status": "FITS_WCS_VALIDATED_DIAGNOSTIC",
                "products": product_info,
                "shape": list(image.shape),
                "fractions": {
                    "finite_image": finite_image / total,
                    "positive_invvar": positive_ivar / total,
                    "positive_nexp": positive_nexp / total,
                    "instrumentally_usable": numerically_valid / total,
                    "satur_or_allmask": saturated / total,
                },
                "warnings": [
                    "FITS/WCS integrity is not a science-quality or sky/PSF acceptance",
                    "Original coadd correlated noise is not inferred from INVVAR",
                ],
            })
    except (OSError, ValueError, RuntimeError, TypeError, IndexError,
            KeyError, AssertionError) as exc:
        result.update({
            "status": "INVALID_FITS",
            "error": f"{type(exc).__name__}: {exc}",
        })
    return result


def _sampled_footprint(data_root: Path, verified: list[dict], field: SkyField,
                       *, pitch_arcsec: float) -> list[dict]:
    """Nearest-pixel common TAN sky grid, grouped by release/region/band.

    This is NOT a resampled science image. Overlaps count once in the union,
    and independent North/South releases are never merged into a sum.
    """
    from astropy.io import fits
    from astropy.wcs import WCS
    if not np.isfinite(pitch_arcsec) or pitch_arcsec <= 0:
        raise ValueError("Sample pitch must be finite and positive")
    count = math.ceil(field.width_arcmin * 60 / pitch_arcsec)
    if count * count > MAX_GRID_CELLS:
        raise ValueError(
            f"Requested sky grid has {count*count} cells; "
            f"limit is {MAX_GRID_CELLS}. Increase pitch_arcsec.")
    # Each sampled cell uses the center of one common tangent-plane element.
    step_deg = field.width_arcmin / (60 * count)
    sample_wcs = WCS(naxis=2)
    sample_wcs.wcs.crval = [field.ra_deg, field.dec_deg]
    sample_wcs.wcs.crpix = [(count+1)/2, (count+1)/2]
    sample_wcs.wcs.cdelt = [-step_deg, step_deg]
    sample_wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    yy, xx = np.indices((count, count), dtype=float)
    ra, dec = sample_wcs.all_pix2world(xx, yy, 0)
    flat_ra, flat_dec = ra.ravel(), dec.ravel()
    grouped = defaultdict(list)
    for dataset in verified:
        if dataset["status"] == "FITS_WCS_VALIDATED_DIAGNOSTIC":
            grouped[(dataset["release"], dataset["region"],
                     dataset["band"])].append(dataset)
    reports = []
    for (release, region, band), entries in sorted(grouped.items()):
        geometric_count = np.zeros(count*count, dtype=np.uint16)
        valid_count = np.zeros(count*count, dtype=np.uint16)
        per_brick = []
        for entry in entries:
            key = (release, region, entry["brick"], band)
            paths = _expected_paths(data_root, key)
            with ExitStack() as stack:
                hdus = {k: stack.enter_context(
                    fits.open(p, memmap=True)) for k, p in paths.items()}
                arrays, native_wcs, _, _ = _validate_open_hdus(hdus, band)
                xp, yp = native_wcs.all_world2pix(flat_ra, flat_dec, 0)
                bounded = np.isfinite(xp) & np.isfinite(yp)
                x = np.zeros(xp.size, dtype=np.int64)
                y = np.zeros(yp.size, dtype=np.int64)
                x[bounded] = np.rint(xp[bounded]).astype(np.int64)
                y[bounded] = np.rint(yp[bounded]).astype(np.int64)
                bounded &= (x >= 0) & (y >= 0) & (
                    x < arrays["image"].shape[1]) & (y < arrays["image"].shape[0])
                idx = np.flatnonzero(bounded)
                if idx.size:
                    xb, yb = x[idx], y[idx]
                    finite = np.isfinite(arrays["image"][yb, xb])
                    inv = arrays["invvar"][yb, xb]
                    exposure = arrays["nexp"][yb, xb]
                    bits = maskbits_to_uint(arrays["maskbits"][yb, xb])
                    flagged = ((bits & np.uint32(
                        (1 << LEGACY_BITS[f"SATUR_{band.upper()}"]) |
                        (1 << LEGACY_BITS[f"ALLMASK_{band.upper()}"]))) != 0)
                    clean = (finite & np.isfinite(inv) & (inv > 0)
                             & np.isfinite(exposure) & (exposure > 0)
                             & ~flagged)
                    valid_count[idx[clean]] += 1
                    geometric_count[idx] += 1
                    covered = int(np.count_nonzero(clean))
                else:
                    covered = 0
                per_brick.append({
                    "brick": entry["brick"],
                    "sampled_inside": int(idx.size),
                    "sampled_usable": covered})
        samples = count*count
        reports.append({
            "release": release, "region": region, "band": band,
            "grid_shape": [count, count],
            "sample_pitch_arcsec": field.width_arcmin * 60 / count,
            "samples": samples,
            "unique_geometric_samples": int(np.count_nonzero(geometric_count)),
            "unique_usable_samples": int(np.count_nonzero(valid_count)),
            "overlapping_geometric_samples": int(np.count_nonzero(
                geometric_count > 1)),
            "overlapping_usable_samples": int(np.count_nonzero(
                valid_count > 1)),
            "geometric_sample_fraction": float(np.count_nonzero(
                geometric_count) / samples),
            "instrumentally_usable_sample_fraction": float(np.count_nonzero(
                valid_count) / samples),
            "per_brick": per_brick,
            "status": "SAMPLED_DIAGNOSTIC_NOT_ADOPTED",
            "warning": (
                "Nearest-native-pixel TAN grid diagnostic, not exact area, "
                "a stitched mosaic, a sky model or science-ready image"),
        })
    return reports


def audit_dataset_root(data_root: str | Path, *, field: SkyField | None = None,
                       pitch_arcsec: float = 2.0, max_datasets: int = MAX_DATASETS,
                       progress=None) -> dict:
    """Run optional deep FITS/WCS audit and grouped multi-brick screening."""
    root = Path(data_root).expanduser().resolve()
    inventory = discover_dataset_inventory(root)
    if len(inventory) > max_datasets:
        raise ValueError(f"{len(inventory)} datasets exceeds audit limit {max_datasets}")
    records = []
    for i, entry in enumerate(inventory):
        if progress:
            progress((i, len(inventory), entry["dataset_id"]))
        records.append(verify_dataset(root, entry))
    groups = _sampled_footprint(
        root, records, field, pitch_arcsec=pitch_arcsec) if field else []
    failures = sum(e["status"] in ("INVALID_FITS", "EMPTY_FILE",
                                   "INCOMPLETE_FILES", "ORPHAN_MASKBITS")
                   for e in records)
    return {
        "schema": SCHEMA, "created_utc": datetime.now(timezone.utc).isoformat(),
        "science_status": "DIAGNOSTIC_NOT_ADOPTED",
        "data_root": str(root),
        "field": None if field is None else {
            "ra_deg": field.ra_deg, "dec_deg": field.dec_deg,
            "width_arcmin": field.width_arcmin},
        "datasets": records, "multi_brick_groups": groups,
        "counts": {
            "total": len(records),
            "validated": sum(e["status"] == "FITS_WCS_VALIDATED_DIAGNOSTIC"
                             for e in records),
            "incomplete_or_invalid": failures,
        },
        "warnings": [
            "No catalog completeness, absolute sky, PSF, astrometry refinement "
            "or photometric accuracy is certified",
            "North and South are separate reductions, never summed",
            "Sampled sky-grid fractions do not establish exact fractional "
            "pixel overlap or a science mosaic",
        ],
    }


def save_dataset_audit(report: dict, project_root: str | Path, *,
                       project_id: str | None = None) -> Path:
    """Persist immutable audit receipt outside original FITS / data subtree."""
    root = Path(project_root).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Project directory does not exist: {root}")
    if report.get("schema") != SCHEMA:
        raise ValueError("Unexpected dataset audit schema")
    run_id = (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
              + "-" + secrets.token_hex(5))
    out = root / "dataset-audits" / run_id
    out.mkdir(parents=True, exist_ok=False)
    record = dict(report)
    record["project_id"] = project_id
    record["run_id"] = run_id
    path = out / "audit.json"
    _atomic_json(path, record)
    return path
