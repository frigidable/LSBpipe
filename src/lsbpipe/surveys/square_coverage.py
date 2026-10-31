"""P3 per-pixel diagnostic of the *entire* requested ICRS square in one brick.

The square is defined in the local TAN plane, matching the official
Legacy Viewer polygon. Statistics are measured on actual pixel centers.
An incomplete brick is reported, not padded, reprojected or accepted.
No galaxy photometry or clean-sky decision is made here.
"""
from __future__ import annotations

from contextlib import ExitStack
from dataclasses import asdict, dataclass
from pathlib import Path
import json
import math

import numpy as np

from lsbpipe.survey import LEGACY_BITS, _check_wcs_compatible, maskbits_to_uint
from .coverage import _science_hdu_index
from .legacy import BrickHit, SkyField, product_for, regions_for_release
from .viewer import field_polygon


AUDIT_BITS = (
    "NPRIMARY", "BRIGHT", "MEDIUM", "GALAXY", "CLUSTER", "BAILOUT",
)


@dataclass(frozen=True)
class SquareCoverage:
    status: str
    release: str
    region: str
    brick: str
    band: str
    center_ra_dec_deg: tuple[float, float]
    side_arcmin: float
    shape: tuple[int, int]
    polygon_within_single_brick: bool
    approximate_geometric_fraction: float
    pixel_centers_sampled: int
    estimated_square_pixel_centers: float
    pixel_area_arcsec2: float
    finite_image_fraction: float | None
    positive_ivar_fraction: float | None
    positive_nexp_fraction: float | None
    instrumentally_usable_fraction: float | None
    instrumentally_usable_pixels: int
    mean_nexp_where_positive: float | None
    nexp_percentiles_positive: dict[str, float] | None
    maskbit_counts: dict[str, int]
    maskbit_fractions: dict[str, float | None]
    warnings: tuple[str, ...]
    hdu_indices: dict[str, int]
    header_units: dict[str, str | None]
    files: dict[str, str]

    def to_dict(self):
        return asdict(self)


def _project_tan(ra_deg: np.ndarray, dec_deg: np.ndarray,
                 ra0_deg: float, dec0_deg: float):
    """Exact gnomonic coordinate, tan(angle), on target's local ICRS plane."""
    ra = np.deg2rad(ra_deg)
    dec = np.deg2rad(dec_deg)
    a = math.radians(ra0_deg)
    d = math.radians(dec0_deg)
    dra = ra - a
    cd = np.cos(dec)
    sd = np.sin(dec)
    denom = math.sin(d) * sd + math.cos(d) * cd * np.cos(dra)
    east = cd * np.sin(dra) / denom
    north = (math.cos(d) * sd - math.sin(d) * cd * np.cos(dra)) / denom
    return east, north, denom


def square_coverage_from_arrays(
    image, invvar, nexp, maskbits, wcs,
    *, field: SkyField, band: str, release: str = "dr9",
    region: str = "south", brick: str = "synthetic",
    chunk_rows: int = 128, files: dict[str, str] | None = None,
    hdu_indices: dict[str, int] | None = None,
    header_units: dict[str, str | None] | None = None,
) -> SquareCoverage:
    """Compute four-layer screening and exact square membership per sky pixel.

    The footprint estimate is based on local WCS pixel area and is explicitly
    approximate. A separate polygon-within-brick check is not replaced by
    pixel-count ratios that might round to 100%.
    """
    from astropy.coordinates import SkyCoord
    import astropy.units as u

    if band not in ("g", "r", "i", "z"):
        raise ValueError("Invalid band")
    if not isinstance(chunk_rows, int) or not 1 <= chunk_rows <= 1024:
        raise ValueError("chunk_rows must be in [1,1024]")
    arr = tuple(np.asarray(a) for a in (image, invvar, nexp, maskbits))
    ny, nx = arr[0].shape if arr[0].ndim == 2 else (0, 0)
    if ny == 0 or nx == 0 or any(a.shape != (ny, nx) for a in arr):
        raise ValueError("All four FITS layers must have a common nonempty 2D grid")
    if arr[3].dtype.kind not in "iu" or arr[3].dtype.itemsize not in (2, 4):
        raise ValueError("MASKBITS must be a 16-bit or 32-bit integer raster")
    if not wcs.has_celestial or wcs.pixel_n_dim != 2:
        raise ValueError("Expected a 2D celestial WCS")

    vertices = field_polygon(field, subdivisions=8)
    corners = np.asarray(vertices)
    px, py = wcs.all_world2pix(corners[:, 0], corners[:, 1], 0)
    if not np.all(np.isfinite(px)) or not np.all(np.isfinite(py)):
        raise ValueError("Cannot project all square vertices into the brick")
    fully_inside = bool(
        np.all((px >= 0) & (px <= nx-1) & (py >= 0) & (py <= ny-1)))
    xlo = max(0, int(math.floor(float(np.min(px)))) - 2)
    xhi = min(nx, int(math.ceil(float(np.max(px)))) + 3)
    ylo = max(0, int(math.floor(float(np.min(py)))) - 2)
    yhi = min(ny, int(math.ceil(float(np.max(py)))) + 3)

    center = SkyCoord(field.ra_deg * u.deg, field.dec_deg * u.deg)
    x0, y0 = wcs.world_to_pixel(center)
    east = center.directional_offset_by(90*u.deg, 1*u.arcsec)
    north = center.directional_offset_by(0*u.deg, 1*u.arcsec)
    xe, ye = wcs.world_to_pixel(east)
    xn, yn = wcs.world_to_pixel(north)
    jac = np.array([[xe-x0, xn-x0], [ye-y0, yn-y0]])
    det = float(np.linalg.det(jac))
    if not math.isfinite(det) or abs(det) <= 1e-10:
        raise ValueError("Invalid local pixel-area Jacobian")
    pixel_area = 1 / abs(det)
    expected = (field.width_arcmin * 60.)**2 / pixel_area
    half = math.tan(math.radians(field.width_arcmin / 120.))

    labels = AUDIT_BITS + (f"SATUR_{band.upper()}", f"ALLMASK_{band.upper()}")
    masks = {k: np.uint32(1 << LEGACY_BITS[k]) for k in labels}
    counts = {k: 0 for k in labels}
    seen = finite = ivar_pos = nexp_pos = usable = 0
    exposure_sum = 0.
    positive_exposures = []
    for start in range(ylo, yhi, chunk_rows):
        stop = min(start+chunk_rows, yhi)
        if xlo >= xhi:
            continue
        xs = np.arange(xlo, xhi, dtype=float)
        ys = np.arange(start, stop, dtype=float)
        X, Y = np.meshgrid(xs, ys)
        ra, dec = wcs.all_pix2world(X, Y, 0)
        u_coord, v_coord, denom = _project_tan(
            ra, dec, field.ra_deg, field.dec_deg)
        inside = ((denom > 0) & (np.abs(u_coord) <= half) &
                  (np.abs(v_coord) <= half))
        n = int(np.count_nonzero(inside))
        if not n:
            continue
        seen += n
        cut = (slice(start, stop), slice(xlo, xhi))
        image_part, ivar_part, nexp_part, bits_part = (a[cut] for a in arr)
        good_image = np.isfinite(image_part)
        good_ivar = np.isfinite(ivar_part) & (ivar_part > 0)
        good_nexp = np.isfinite(nexp_part) & (nexp_part > 0)
        bits = maskbits_to_uint(bits_part)
        reject = ((bits & (masks[f"SATUR_{band.upper()}"] |
                           masks[f"ALLMASK_{band.upper()}"])) != 0)
        usable += int(np.count_nonzero(
            inside & good_image & good_ivar & good_nexp & ~reject))
        finite += int(np.count_nonzero(inside & good_image))
        ivar_pos += int(np.count_nonzero(inside & good_ivar))
        positive = inside & good_nexp
        nexp_pos += int(np.count_nonzero(positive))
        nvals = np.asarray(nexp_part[positive], dtype=np.float64)
        exposure_sum += float(np.sum(nvals, dtype=np.float64))
        if nvals.size:
            positive_exposures.append(nvals)
        for name, bit in masks.items():
            counts[name] += int(np.count_nonzero(
                inside & ((bits & bit) != 0)))

    fraction = lambda n: n / seen if seen else None
    pct = None
    if positive_exposures:
        vals = np.concatenate(positive_exposures)
        q = np.percentile(vals, [0, 5, 25, 50, 75, 95, 100])
        pct = dict(zip(("min", "p05", "p25", "median",
                        "p75", "p95", "max"), map(float, q)))
    warnings = [
        "Single-brick square diagnostic, not multi-brick coverage adoption",
        "Correlated coadd noise and PSF/sky contamination are not assessed",
        "SGA GALAXY and Gaia MEDIUM are diagnostic, not automatic target exclusions",
    ]
    if not fully_inside:
        warnings.append("SQUARE_FOOTPRINT_EXTENDS_OUTSIDE_BRICK")
    if not seen:
        warnings.append("SQUARE_NO_PIXEL_CENTERS_IN_BRICK")
    elif usable < seen:
        warnings.append("SQUARE_INVALID_OR_INSTRUMENTALLY_MASKED_PIXELS")
    if counts["GALAXY"]:
        warnings.append("SQUARE_SGA_GALAXY_BIT_PRESENT_DO_NOT_AUTOMASK")
    if counts["MEDIUM"]:
        warnings.append("SQUARE_GAIA_MEDIUM_STAR_WARNING")
    return SquareCoverage(
        status="DIAGNOSTIC_NOT_ADOPTED",
        release=release, region=region, brick=brick, band=band,
        center_ra_dec_deg=(field.ra_deg, field.dec_deg),
        side_arcmin=field.width_arcmin, shape=(ny, nx),
        polygon_within_single_brick=fully_inside,
        approximate_geometric_fraction=min(1., seen/expected),
        pixel_centers_sampled=seen, estimated_square_pixel_centers=expected,
        pixel_area_arcsec2=pixel_area,
        finite_image_fraction=fraction(finite),
        positive_ivar_fraction=fraction(ivar_pos),
        positive_nexp_fraction=fraction(nexp_pos),
        instrumentally_usable_fraction=fraction(usable),
        instrumentally_usable_pixels=usable,
        mean_nexp_where_positive=(exposure_sum/nexp_pos
                                  if nexp_pos else None),
        nexp_percentiles_positive=pct,
        maskbit_counts=counts,
        maskbit_fractions={k:fraction(v) for k,v in counts.items()},
        warnings=tuple(warnings), files=dict(files or {}),
        hdu_indices=dict(hdu_indices or {}),
        header_units=dict(header_units or {}))


def inspect_local_square(directory: Path, *, release: str, region: str,
                         brick: str, band: str, field: SkyField):
    """Open original quartet and independently check all image grids/WCS."""
    from astropy.io import fits
    from astropy.wcs import WCS

    if region not in regions_for_release(release):
        raise ValueError("Invalid survey release/region")
    if band not in (("g","r","z") if region == "north" else ("g","r","i","z")):
        raise ValueError("Band unavailable for this region")
    hit = BrickHit(release, region, brick, 0, 0, 0, 0, {band: 1})
    paths = {
        kind: Path(directory)/product_for(
            hit, kind, None if kind == "maskbits" else band).relative_path
        for kind in ("image", "invvar", "nexp", "maskbits")
    }
    absent = [str(p) for p in paths.values() if not p.is_file()]
    if absent:
        raise FileNotFoundError("Missing original science FITS: " + ", ".join(absent))
    with ExitStack() as stack:
        hdus = {k:stack.enter_context(fits.open(p, memmap=True))
                for k,p in paths.items()}
        indices = {k:_science_hdu_index(hdus[k],k) for k in paths}
        layers = {k:hdus[k][indices[k]].data for k in paths}
        wcs = {k:WCS(hdus[k][indices[k]].header).celestial for k in paths}
        shape = layers["image"].shape
        for kind in ("invvar", "nexp", "maskbits"):
            if layers[kind].shape != shape:
                raise ValueError(f"{kind} shape differs from IMAGE")
            _check_wcs_compatible(wcs["image"], wcs[kind], shape, kind)
        return square_coverage_from_arrays(
            layers["image"], layers["invvar"], layers["nexp"],
            layers["maskbits"], wcs["image"],
            field=field, band=band, region=region, release=release, brick=brick,
            hdu_indices=indices,
            header_units={k:hdus[k][indices[k]].header.get("BUNIT")
                          for k in paths},
            files={k:str(p.resolve()) for k,p in paths.items()})


def save_square_coverage(result: SquareCoverage, path: Path):
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result.to_dict(), indent=2, ensure_ascii=False),
                   encoding="utf-8")
    return out
