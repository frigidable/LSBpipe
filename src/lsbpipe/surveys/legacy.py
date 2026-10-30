"""Release-aware DESI Legacy Surveys brick discovery and product manifests.

All coverage statements are qualified: summary tables describe median exposure
counts within *primary* brick areas, not per-pixel completeness in the requested
cutout. A URL is not considered available until independently checked.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable
import hashlib
import json
import math
import os
import re
import urllib.request

import numpy as np

ARCHIVE = "https://portal.nersc.gov/cfs/cosmo/data/legacysurvey"
REGIONS = {"dr9": ("north", "south"), "dr10": ("south",),
           "dr11": ("north", "south")}
BRICK_RE = re.compile(r"^[0-9]{4}[pm][0-9]{3}$")
BANDS = ("g", "r", "i", "z")
DEFAULT_PRODUCTS = ("image", "invvar", "maskbits", "nexp", "tractor")
PRODUCT_TYPES = ("image", "invvar", "maskbits", "nexp", "tractor",
                 "psfsize", "depth", "galdepth", "model")


@dataclass(frozen=True)
class SkyField:
    ra_deg: float
    dec_deg: float
    width_arcmin: float = 15.0

    def __post_init__(self):
        if not all(math.isfinite(float(v)) for v in (
            self.ra_deg, self.dec_deg, self.width_arcmin)):
            raise ValueError("Coordinates and field size must be finite")
        if not (0 <= self.ra_deg < 360) or not (-90 <= self.dec_deg <= 90):
            raise ValueError("RA must be [0,360), Dec must be [-90,90]")
        if not (0 < self.width_arcmin <= 120):
            raise ValueError("Field width must be >0 and <=120 arcmin")

    @property
    def ra_halfwidth_deg(self) -> float:
        half_dec = self.width_arcmin / 120.0
        extreme = min(90., abs(self.dec_deg) + half_dec)
        if extreme >= 89.99:
            return 180.
        return min(180., half_dec / math.cos(math.radians(extreme)))


@dataclass(frozen=True)
class BrickHit:
    release: str
    region: str
    name: str
    ra1: float
    ra2: float
    dec1: float
    dec2: float
    median_exposures: dict[str, int | None]

    @property
    def bands_with_median_exposure(self) -> tuple[str, ...]:
        return tuple(b for b, n in self.median_exposures.items() if n is not None and n > 0)


@dataclass(frozen=True)
class Product:
    release: str
    region: str
    brick: str
    kind: str
    band: str | None
    url: str
    relative_path: str
    evidence: str = "UNVERIFIED_URL"
    size_bytes: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def regions_for_release(release: str) -> tuple[str, ...]:
    if release not in REGIONS:
        raise ValueError("Release must be dr9, dr10 or dr11")
    return REGIONS[release]


def summary_url(release: str, region: str) -> str:
    if region not in regions_for_release(release):
        raise ValueError("Invalid release-region combination")
    return f"{ARCHIVE}/{release}/{region}/survey-bricks-{release}-{region}.fits.gz"


def geometry_url(release: str) -> str:
    regions_for_release(release)
    return f"{ARCHIVE}/{release}/survey-bricks.fits.gz"


def catalog_cache_dir() -> Path:
    root = os.environ.get("LOCALAPPDATA")
    return (Path(root) / "LSBpipe" / "catalogs" if root else
            Path.home() / ".cache" / "lsbpipe" / "catalogs")


def cache_catalog(url: str, destination: Path, *, max_bytes: int = 600_000_000) -> Path:
    """Retrieve only published catalog URLs to a private, atomic local cache."""
    if not url.startswith(ARCHIVE + "/") or not url.endswith(".fits.gz"):
        raise ValueError("Unexpected archive catalog URL")
    destination = Path(destination)
    if destination.is_file() and destination.stat().st_size:
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_name(destination.name + ".part")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "LSBpipe-survey/0.4"})
        with urllib.request.urlopen(req, timeout=90) as remote, temp.open("wb") as out:
            total = 0
            while True:
                chunk = remote.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError("Catalog exceeds configured download limit")
                out.write(chunk)
        if total == 0:
            raise IOError("Empty catalog response")
        os.replace(temp, destination)
    finally:
        temp.unlink(missing_ok=True)
    return destination


def _text_column(table, name: str):
    cols = {c.lower(): c for c in table.dtype.names or ()}
    if name.lower() not in cols:
        raise ValueError(f"Missing required FITS table column: {name}")
    values = table[cols[name.lower()]]
    return np.char.strip(values.astype(str))


def _num_column(table, name: str):
    cols = {c.lower(): c for c in table.dtype.names or ()}
    if name.lower() not in cols:
        raise ValueError(f"Missing required FITS table column: {name}")
    return np.asarray(table[cols[name.lower()]], dtype=float)


def _summary_index(summary) -> dict[str, int]:
    names = _text_column(summary, "brickname")
    result = {}
    for i, name in enumerate(names):
        if name in result:
            raise ValueError(f"Duplicate brick in release summary: {name}")
        result[str(name)] = i
    return result


def find_bricks(field: SkyField, geometry, summary, release: str,
                region: str) -> list[BrickHit]:
    """Conservative tangent-field bounding-box intersection; RA wraps at 0."""
    if region not in regions_for_release(release):
        raise ValueError("Unsupported release/region")
    ra1, ra2 = _num_column(geometry, "ra1"), _num_column(geometry, "ra2")
    dec1, dec2 = _num_column(geometry, "dec1"), _num_column(geometry, "dec2")
    names = _text_column(geometry, "brickname")
    if not (len(names) == len(ra1) == len(ra2) == len(dec1) == len(dec2)):
        raise ValueError("Inconsistent geometry table")
    # Brick RA intervals may cross 0: use their circular midpoint and length.
    span = (ra2 - ra1) % 360.
    mid = (ra1 + span / 2) % 360.
    delta = np.abs((mid - field.ra_deg + 180) % 360 - 180)
    half = field.width_arcmin / 120.
    candidates = np.flatnonzero(
        (delta <= field.ra_halfwidth_deg + span / 2) &
        (dec2 >= field.dec_deg - half) &
        (dec1 <= field.dec_deg + half))
    index = _summary_index(summary)
    summaries = {b.lower(): b for b in summary.dtype.names or ()}
    allowed = ("g", "r", "z") if region == "north" else BANDS
    results = []
    for row in candidates:
        brick = str(names[row])
        position = index.get(brick)
        if position is None:
            continue  # geometric brick exists, not represented in release summary
        nexp: dict[str, int | None] = {}
        for band in allowed:
            column = summaries.get("nexp_" + band)
            nexp[band] = (int(summary[column][position]) if column is not None else None)
        results.append(BrickHit(release, region, brick,
                               float(ra1[row]), float(ra2[row]),
                               float(dec1[row]), float(dec2[row]), nexp))
    return sorted(results, key=lambda b: (b.region, b.name))


def discover_bricks(field: SkyField, releases: Iterable[str] = ("dr9",),
                    *, cache_dir: Path | None = None) -> list[BrickHit]:
    """Read official geometry + per-release summaries; requires Astropy and network
    on first use. A failed catalog read raises: *absence of data* is never
    inferred from a network or parsing failure.
    """
    try:
        from astropy.io import fits
    except ImportError as exc:
        raise RuntimeError("Install Astropy to read Legacy catalog FITS") from exc
    cache = Path(cache_dir) if cache_dir is not None else catalog_cache_dir()
    all_hits: list[BrickHit] = []
    for release in dict.fromkeys(releases):
        regions = regions_for_release(release)
        path = cache_catalog(geometry_url(release), cache / release / "survey-bricks.fits.gz")
        with fits.open(path) as hdul:
            geometry = hdul[1].data
            for region in regions:
                source = summary_url(release, region)
                local = cache_catalog(source, cache / release / region /
                                      f"survey-bricks-{release}-{region}.fits.gz")
                with fits.open(local) as other:
                    all_hits.extend(find_bricks(field, geometry, other[1].data,
                                                release, region))
    return all_hits


def product_for(hit: BrickHit, kind: str, band: str | None = None) -> Product:
    if not BRICK_RE.fullmatch(hit.name):
        raise ValueError("Invalid brick name")
    if kind not in PRODUCT_TYPES:
        raise ValueError("Unknown product type")
    if hit.region not in regions_for_release(hit.release):
        raise ValueError("Unsupported release/region")
    if kind not in ("tractor", "maskbits"):
        if band not in hit.median_exposures:
            raise ValueError("Band is unavailable for this release/region")
    elif band is not None:
        raise ValueError("Tractor and MASKBITS files are not band-specific")
    stem = f"legacysurvey-{hit.name}"
    filename = (f"tractor-{hit.name}.fits" if kind == "tractor" else
                f"{stem}-maskbits.fits.fz" if kind == "maskbits" else
                f"{stem}-{kind}-{band}.fits.fz")
    folder = hit.name[:3]
    archive_path = (f"{hit.release}/{hit.region}/tractor/{folder}/{filename}"
                    if kind == "tractor" else
                    f"{hit.release}/{hit.region}/coadd/{folder}/{hit.name}/{filename}")
    return Product(hit.release, hit.region, hit.name, kind, band,
                   f"{ARCHIVE}/{archive_path}", archive_path)


def build_manifest(field: SkyField, hits: Iterable[BrickHit],
                   *, kinds: Iterable[str] = DEFAULT_PRODUCTS,
                   bands: Iterable[str] = BANDS,
                   max_bricks: int = 48) -> dict:
    """Build an explicitly *unverified* product plan, never a claim of availability."""
    selected = list(hits)
    kinds = tuple(dict.fromkeys(kinds))
    bands = tuple(dict.fromkeys(bands))
    if len(selected) > max_bricks:
        raise ValueError(f"{len(selected)} bricks exceed max_bricks={max_bricks}; narrow FOV")
    if any(k not in PRODUCT_TYPES for k in kinds):
        raise ValueError("Unknown product kind")
    if any(b not in BANDS for b in bands):
        raise ValueError("Unsupported band")
    products: list[Product] = []
    seen = set()
    for hit in selected:
        for kind in kinds:
            needed = (None,) if kind in ("tractor", "maskbits") else (
                b for b in bands if b in hit.median_exposures)
            for band in needed:
                product = product_for(hit, kind, band)
                if product.relative_path not in seen:
                    products.append(product)
                    seen.add(product.relative_path)
    # Keep each archive release. DR11 north re-reduces historical DR9 input
    # observations, but distinct FITS products must not be silently collapsed.
    footprints = {}
    for hit in selected:
        footprints.setdefault((hit.region, hit.name), set()).add(hit.release)
    shared_inputs = [
        {"region": region, "brick": brick,
         "releases": sorted(releases),
         "relation": "SHARED_HISTORICAL_INPUT_OBSERVATIONS_NOT_IDENTICAL_PRODUCTS"}
        for (region, brick), releases in sorted(footprints.items())
        if region == "north" and {"dr9", "dr11"}.issubset(releases)
    ]
    return {
        "schema": "lsbpipe.legacy-download/0.1",
        "unique_spatial_bricks": len(footprints),
        "related_release_groups": shared_inputs,
        "field": asdict(field),
        "brick_count": len(selected),
        "bricks": [asdict(h) for h in selected],
        "products": [p.to_dict() for p in products],
        "status": "CANDIDATE_URLS_NOT_VERIFIED",
        "limitations": [
            "Summary NEXP is a median over each brick primary area; it is not a footprint completeness map",
            "Product URL existence requires an explicit HEAD check or download",
            "Actual per-pixel coverage requires downloaded NEXP/INVVAR maps",
            "Selection uses a conservative sky bounding box, not exact spherical polygon intersection",
            "Northern DR9 and DR11 reuse observational input, but may have distinct reduction products; do not automatically deduplicate",
        ],
    }


def save_manifest(manifest: dict, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False),
                    encoding="utf-8")
    return path
