"""P2: diagnostic per-pixel coverage of local Legacy brick FITS.

This is *not* an LSB photometric-quality decision. Input is the actual
NEXP/INVVAR/IMAGE/MASKBITS rasters, not median survey-bricks table NEXP.
Region shapes are local tangent-plane circular samples for triage, not
source isophotes or sky-fitting regions.
"""
from __future__ import annotations

from contextlib import ExitStack
from dataclasses import asdict, dataclass
from pathlib import Path
import json
import math
import numpy as np

from .legacy import SkyField
from lsbpipe.survey import LEGACY_BITS, _check_wcs_compatible


@dataclass(frozen=True)
class RegionCoverage:
    label: str
    pixel_centers_sampled: int
    estimated_geometric_pixel_centers: float
    estimated_footprint_fraction: float
    finite_image_fraction: float | None
    positive_ivar_fraction: float | None
    positive_nexp_fraction: float | None
    usable_fraction: float | None
    usable_pixels: int
    mean_nexp_where_positive: float | None
    bright_warning_fraction: float | None
    galaxy_warning_fraction: float | None


@dataclass(frozen=True)
class CoverageResult:
    status: str
    band: str
    shape: tuple[int, int]
    center_ra_dec_deg: tuple[float, float]
    target_radius_arcsec: float
    sky_inner_arcsec: float
    sky_outer_arcsec: float
    pixel_area_arcsec2: float
    regions: tuple[RegionCoverage, RegionCoverage]
    warnings: tuple[str, ...]
    files: dict[str, str]

    def to_dict(self):
        return asdict(self)


def coverage_from_arrays(
    image, invvar, nexp, maskbits, wcs,
    *, ra_deg: float, dec_deg: float, band: str,
    target_radius_arcsec: float, sky_inner_arcsec: float,
    sky_outer_arcsec: float, chunk_rows: int = 256,
    files: dict[str, str] | None = None,
) -> CoverageResult:
    """Quantify pixel coverage, with strict mask and NEXP semantics.

    Invalid, saturated or ALLMASK_B pixels are unusable for this *conservative*
    screening policy. BRIGHT and GALAXY are warnings, not automatic exclusions.
    No reconstructed pixels, surface-brightness thresholds or sky subtraction.
    """
    from astropy.coordinates import SkyCoord
    import astropy.units as u

    band = band.lower()
    if band not in ('g', 'r', 'i', 'z'):
        raise ValueError("band must be g, r, i or z")
    values = [target_radius_arcsec, sky_inner_arcsec, sky_outer_arcsec]
    if not all(math.isfinite(float(v)) for v in values):
        raise ValueError("Radii must be finite")
    if not (0 < target_radius_arcsec <= sky_inner_arcsec < sky_outer_arcsec <= 1200):
        raise ValueError("Require 0 < target radius <= sky inner < sky outer <=1200 arcsec")
    if not isinstance(chunk_rows, int) or not 1 <= chunk_rows <= 2048:
        raise ValueError("chunk_rows must be integer in [1,2048]")
    field = SkyField(float(ra_deg), float(dec_deg), 1.0)
    arrays = (np.asarray(image), np.asarray(invvar),
              np.asarray(nexp), np.asarray(maskbits))
    shape = arrays[0].shape
    if len(shape) != 2 or not all(a.shape == shape for a in arrays):
        raise ValueError("All four science layers must share the same 2D grid")
    if (arrays[3].dtype.kind not in "iu" or
            arrays[3].dtype.itemsize not in (2, 4)):
        raise ValueError("MASKBITS must be 16/32-bit integer raster")
    if not getattr(wcs, 'has_celestial', False):
        raise ValueError("A celestial WCS is required")

    center = SkyCoord(field.ra_deg * u.deg, field.dec_deg * u.deg, frame="icrs")
    east = center.directional_offset_by(90 * u.deg, 1 * u.arcsec)
    north = center.directional_offset_by(0 * u.deg, 1 * u.arcsec)
    x0, y0 = wcs.world_to_pixel(center)
    xe, ye = wcs.world_to_pixel(east)
    xn, yn = wcs.world_to_pixel(north)
    jac = np.array([[xe-x0, xn-x0], [ye-y0, yn-y0]], dtype=float)
    determinant = float(np.linalg.det(jac))
    if not np.isfinite(determinant) or abs(determinant) < 1e-10:
        raise ValueError("Ill-conditioned local WCS Jacobian")
    pix_area = 1. / abs(determinant)
    inv_jac = np.linalg.inv(jac)
    ny, nx = shape
    # Expected pixel-centre count is approximate, used ONLY to diagnose
    # incomplete brick footprint. Exact spherical area is not inferred.
    expectation = (
        math.pi*target_radius_arcsec**2 / pix_area,
        math.pi*(sky_outer_arcsec**2 - sky_inner_arcsec**2) / pix_area,
    )
    stats = [
        {"sample":0,"image":0,"ivar":0,"nexp":0,"usable":0,
         "nexp_sum":0.,"bright":0,"galaxy":0}
        for _ in range(2)
    ]
    bit_satur = np.int64(1 << LEGACY_BITS['SATUR_' + band.upper()])
    bit_all = np.int64(1 << LEGACY_BITS['ALLMASK_' + band.upper()])
    bit_bright = np.int64(1 << LEGACY_BITS['BRIGHT'])
    bit_galaxy = np.int64(1 << LEGACY_BITS['GALAXY'])
    xs = np.arange(nx, dtype=float)[None, :] - float(x0)
    for start in range(0, ny, chunk_rows):
        stop = min(start + chunk_rows, ny)
        ys = np.arange(start, stop, dtype=float)[:, None] - float(y0)
        # Local true east/north tangent displacements, in arcseconds.
        east_arc = inv_jac[0, 0]*xs + inv_jac[0, 1]*ys
        north_arc = inv_jac[1, 0]*xs + inv_jac[1, 1]*ys
        radius2 = east_arc**2 + north_arc**2
        target = radius2 <= target_radius_arcsec**2
        sky = ((radius2 >= sky_inner_arcsec**2) &
               (radius2 <= sky_outer_arcsec**2))
        image_part, ivar_part, nexp_part, bits_part = (
            array[start:stop] for array in arrays)
        if (bits_part.dtype.kind == "i" and bits_part.dtype.itemsize == 4
                and np.any(bits_part < 0)):
            raise ValueError("Negative signed-32 MASKBITS unsupported")
        data_ok = np.isfinite(image_part)
        weight_ok = np.isfinite(ivar_part) & (ivar_part > 0)
        exposure_ok = np.isfinite(nexp_part) & (nexp_part > 0)
        flags = bits_part.astype(np.int64, copy=False)
        instrument_ok = (flags & (bit_satur | bit_all)) == 0
        usable = data_ok & weight_ok & exposure_ok & instrument_ok
        bright = (flags & bit_bright) != 0
        galaxy = (flags & bit_galaxy) != 0
        for mask, info in zip((target, sky), stats):
            n = int(np.count_nonzero(mask))
            if not n:
                continue
            info["sample"] += n
            info["image"] += int(np.count_nonzero(data_ok & mask))
            info["ivar"] += int(np.count_nonzero(weight_ok & mask))
            positive = exposure_ok & mask
            info["nexp"] += int(np.count_nonzero(positive))
            info["nexp_sum"] += float(np.sum(nexp_part[positive], dtype=np.float64))
            info["usable"] += int(np.count_nonzero(usable & mask))
            info["bright"] += int(np.count_nonzero(bright & mask))
            info["galaxy"] += int(np.count_nonzero(galaxy & mask))

    regions = []
    warnings = [
        "Coadd resampling creates correlated noise; this report does not calculate independent-pixel uncertainties",
        "Conservative per-band SATUR/ALLMASK policy; MASKBITS BRIGHT/GALAXY remain diagnostic",
        "Circular tangent-plane regions are screening geometry, not galaxy isophotes",
    ]
    for label, stat, expected in zip(("target", "sky_annulus"), stats, expectation):
        n = stat["sample"]
        footprint = min(1., n / expected) if expected else 0.
        def fraction(key):
            return stat[key] / n if n else None
        reg = RegionCoverage(
            label, n, expected, footprint,
            fraction("image"), fraction("ivar"), fraction("nexp"),
            fraction("usable"), stat["usable"],
            stat["nexp_sum"] / stat["nexp"] if stat["nexp"] else None,
            fraction("bright"), fraction("galaxy"))
        regions.append(reg)
        if n == 0:
            warnings.append(f"{label.upper()}_NO_PIXEL_CENTERS")
        elif footprint < .95:
            warnings.append(f"{label.upper()}_APPROX_FOOTPRINT_INCOMPLETE")
        if n and stat["usable"] < n:
            warnings.append(f"{label.upper()}_INVALID_OR_MASKED_PIXELS")
    return CoverageResult(
        status="DIAGNOSTIC_NOT_ADOPTED",
        band=band, shape=tuple(int(v) for v in shape),
        center_ra_dec_deg=(field.ra_deg, field.dec_deg),
        target_radius_arcsec=float(target_radius_arcsec),
        sky_inner_arcsec=float(sky_inner_arcsec),
        sky_outer_arcsec=float(sky_outer_arcsec),
        pixel_area_arcsec2=float(pix_area),
        regions=tuple(regions), warnings=tuple(warnings),
        files=dict(files or {}),
    )


def inspect_local_brick(
    directory: Path, *, release: str, region: str, brick: str,
    band: str, ra_deg: float, dec_deg: float,
    target_radius_arcsec: float, sky_inner_arcsec: float,
    sky_outer_arcsec: float, chunk_rows: int = 256,
) -> CoverageResult:
    """Read downloaded original science FITS, validate WCS and inspect footprint.

    IMAGE/INVVAR/NEXP use primary HDU, optical MASKBITS use HDU 1.
    The loader never silently reprojections, interpolates or fills pixels.
    """
    from astropy.io import fits
    from astropy.wcs import WCS
    from .legacy import BrickHit, product_for, regions_for_release
    if region not in regions_for_release(release):
        raise ValueError("Invalid release/region")
    if band not in (('g', 'r', 'z') if region == 'north' else ('g', 'r', 'i', 'z')):
        raise ValueError("Band unavailable for region")
    dummy = BrickHit(release, region, brick, 0, 0, 0, 0, {band:1})
    paths = {kind: Path(directory) / product_for(
        dummy, kind, None if kind == 'maskbits' else band).relative_path
        for kind in ("image", "invvar", "nexp", "maskbits")}
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError("Missing required science FITS: " + ", ".join(missing))
    with ExitStack() as stack:
        hdus = {kind: stack.enter_context(fits.open(path, memmap=True))
                for kind, path in paths.items()}
        hdus_main = {kind: hdus[kind][1 if kind == 'maskbits' else 0]
                     for kind in paths}
        arrays = {kind: hdus_main[kind].data for kind in paths}
        if any(array is None or array.ndim != 2 for array in arrays.values()):
            raise ValueError("Expected nonempty 2D image HDUs")
        wcs = {kind: WCS(hdus_main[kind].header).celestial for kind in paths}
        reference_shape = arrays["image"].shape
        for kind in ("invvar", "nexp", "maskbits"):
            if arrays[kind].shape != reference_shape:
                raise ValueError(f"{kind} shape mismatch")
            _check_wcs_compatible(wcs["image"], wcs[kind],
                                  reference_shape, kind)
        return coverage_from_arrays(
            arrays["image"], arrays["invvar"], arrays["nexp"], arrays["maskbits"],
            wcs["image"], ra_deg=ra_deg, dec_deg=dec_deg, band=band,
            target_radius_arcsec=target_radius_arcsec,
            sky_inner_arcsec=sky_inner_arcsec,
            sky_outer_arcsec=sky_outer_arcsec, chunk_rows=chunk_rows,
            files={k: str(v.resolve()) for k,v in paths.items()})


def save_coverage(result: CoverageResult, output: Path):
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result.to_dict(), indent=2, ensure_ascii=False),
                    encoding="utf-8")
    return path
