"""Optional DESI Legacy PSFSIZE seeing prior, never an adopted ePSF.

Legacy coadd psfsize-<band>.fits.fz gives *weighted average PSF FWHM
in arcseconds at each native coadd pixel*. If an original map is already
present on disk, sample it near the project's ICRS coordinate using
verified map-vs-native-image WCS. Convert to native-image pixels from
actual WCS (do not assume 0.262 arcsec/pix).
Missing/invalid/unsupported maps are an explicit absence, NOT a reason
to silently download or invent an empirical measured seeing.

The galaxy-protection radius is conceptually separate and cannot be
inferred from the PSF seeing map.
"""
from __future__ import annotations

from hashlib import sha256
import math
from pathlib import Path

import numpy as np


def _suggest_detection_fwhm_from_native_map(frame, *, target_icrs_deg):
    from astropy.io import fits
    from astropy.wcs import WCS
    from astropy.wcs.utils import proj_plane_pixel_scales

    files = frame.source_evidence.get("files", {})
    source = files.get("image")
    if not source:
        raise ValueError("Native original FITS provenance is required")
    image_path = Path(source)
    marker = f"-image-{frame.band}.fits.fz"
    if not image_path.name.endswith(marker):
        raise ValueError("Unsupported image path for matching PSFSIZE map")
    path = image_path.with_name(
        image_path.name[:-len(marker)] + f"-psfsize-{frame.band}.fits.fz")
    if not path.is_file() or path.is_symlink():
        return {
            "available": False,
            "source": "OPTIONAL_DESI_LOCAL_PSF_SIZE_MAP_ABSENT",
            "message": ("The per-band PSFSIZE map is not stored in the "
                        "current Leo P native FITS project. Manual FWHM "
                        "guess remains an explicit heuristic."),
        }
    with fits.open(path, memmap=True) as hdul:
        images = [hdu for hdu in hdul if getattr(hdu, "data", None) is not None
                  and getattr(hdu.data, "ndim", None) == 2]
        if len(images) != 1:
            raise ValueError("PSFSIZE map needs exactly one native 2D image")
        hdu = images[0]
        if tuple(hdu.data.shape) != frame.shape:
            raise ValueError("PSFSIZE map shape differs from original IMAGE")
        map_wcs = WCS(hdu.header).celestial
        source_wcs = frame.wcs.celestial
        x, y = source_wcs.all_world2pix(
            float(target_icrs_deg[0]), float(target_icrs_deg[1]), 0)
        if not np.isfinite(x) or not np.isfinite(y):
            raise ValueError("Nonfinite target center in native IMAGE WCS")
        ix, iy = int(np.rint(x)), int(np.rint(y))
        ny, nx = frame.shape
        if ix < 2 or ix >= nx-2 or iy < 2 or iy >= ny-2:
            raise ValueError("Target outside safe PSFSIZE sample footprint")
        probe_x, probe_y = map_wcs.all_world2pix(
            float(target_icrs_deg[0]), float(target_icrs_deg[1]), 0)
        if (not np.isfinite(probe_x) or not np.isfinite(probe_y) or
                np.hypot(probe_x-x, probe_y-y) > .05):
            raise ValueError("PSFSIZE map WCS differs at Leo P target")
        # Check an additional off-center position to reject different scale
        # or orientation despite matching target CRVAL.
        world0, world1 = source_wcs.all_pix2world(
            np.array([x+35., x]), np.array([y, y+35.]), 0)
        test_x, test_y = map_wcs.all_world2pix(world0, world1, 0)
        offset = np.hypot(test_x-np.array([x+35., x]),
                          test_y-np.array([y, y+35.]))
        if not np.all(np.isfinite(offset)) or float(np.max(offset)) > .05:
            raise ValueError("PSFSIZE map WCS scale/orientation mismatch")
        patch = np.asarray(hdu.data[iy-2:iy+3, ix-2:ix+3], dtype=float)
        samples = patch[np.isfinite(patch) & (patch > 0)]
        if len(samples) < 12:
            raise ValueError("PSFSIZE map lacks positive finite data near Leo P")
        arcsec = float(np.median(samples))
    pixscales = np.asarray(proj_plane_pixel_scales(frame.wcs.celestial),
                           dtype=float)*3600.
    if (not np.isfinite(pixscales).all()
            or np.min(pixscales) <= 0 or
            np.max(pixscales)/np.min(pixscales) > 1.05):
        raise ValueError("Unexpected anisotropic/invalid native image WCS scale")
    scale = float(np.mean(pixscales))
    fwhm = arcsec/scale
    if not (0.2 <= arcsec <= 8. and 2.5 <= fwhm <= 20.):
        raise ValueError(
            f"PSFSIZE {arcsec:.3g} arcsec -> {fwhm:.3g} px is outside "
            "the initial DAOStarFinder recipe's validated range")
    digest = sha256()
    with path.open("rb") as reader:
        for block in iter(lambda: reader.read(4*1024*1024), b""):
            digest.update(block)
    return {
        "available": True,
        "source": "LOCAL_LEGACY_PSF_SIZE_MAP_NOT_EMPIRICAL_EPSF",
        "path": str(path),
        "source_sha256_local": digest.hexdigest(),
        "band": frame.band,
        "image_pixel_scale_arcsec": scale,
        "psfsize_arcsec": arcsec,
        "suggested_detection_fwhm_pix": fwhm,
        "sample": "5x5 median at project ICRS coordinate",
        "is_measured_empirical_psf": False,
        "requires_researcher_review": True,
    }


def _suggest_from_native_tractor_psfsize(frame, *, target_icrs_deg):
    """Local-only alternative when a same-band coadd PSFSIZE map is unusable.

    Legacy DR11 Tractor PSFSIZE_<band> is the weighted-average seeing
    FWHM at each source *position*, not a stellar ePSF measurement.
    Use the median of nearby brick-primary rows, never a whole-brick
    mean or one arbitrary galaxy's entry.
    """
    from astropy.io import fits
    from astropy.wcs.utils import proj_plane_pixel_scales
    from astropy.coordinates import SkyCoord
    import astropy.units as u

    from lsbpipe.photometry.psf_tractor_gaia import (
        MAX_CATALOG_BYTES, tractor_catalog_location,
    )

    image_path = Path(frame.source_evidence.get("files", {}).get("image", ""))
    if not image_path.is_file() or image_path.is_symlink():
        return None
    if frame.release != "dr11" or frame.region != "south":
        return None
    path, _ = tractor_catalog_location(
        frame.source_evidence.get("data_root", image_path.parent),
        release=frame.release, region=frame.region, brick=frame.brick)
    if not path.is_file() or path.is_symlink():
        return None
    stat = path.stat()
    if not 200 < stat.st_size <= MAX_CATALOG_BYTES:
        raise ValueError("Local Tractor catalogue outside safe size range")
    band_col = "PSFSIZE_" + frame.band.upper()
    with fits.open(path, memmap=True) as hdus:
        tables = [h for h in hdus if isinstance(h, fits.BinTableHDU)]
        if len(tables) != 1:
            raise ValueError("Native Tractor seeing prior requires one table")
        table = tables[0].data
        names = {name.upper(): name for name in tables[0].columns.names}
        required = {"RA", "DEC", "BRICKNAME", "BRICK_PRIMARY", band_col}
        if required - set(names):
            return None
        ras, decs, sizes = [], [], []
        for row in table:
            label = row[names["BRICKNAME"]]
            label = (label.decode("ascii", errors="replace")
                     if isinstance(label, bytes) else str(label)).strip()
            if label != frame.brick:
                raise ValueError(
                    "Tractor PSFSIZE source has wrong brick identity")
            if not bool(row[names["BRICK_PRIMARY"]]):
                continue
            ra = float(row[names["RA"]])
            dec = float(row[names["DEC"]])
            value = float(row[names[band_col]])
            if (np.isfinite(ra) and 0 <= ra < 360 and
                    np.isfinite(dec) and -90 <= dec <= 90 and
                    np.isfinite(value) and .2 <= value <= 8.):
                ras.append(ra)
                decs.append(dec)
                sizes.append(value)
    if len(sizes) < 8:
        return None
    target = SkyCoord(
        ra=float(target_icrs_deg[0])*u.deg,
        dec=float(target_icrs_deg[1])*u.deg, frame="icrs")
    locations = SkyCoord(
        ra=np.asarray(ras)*u.deg,
        dec=np.asarray(decs)*u.deg, frame="icrs")
    offsets = locations.separation(target).arcsec
    selection = None
    radius = None
    for candidate_radius in (90., 180., 240.):
        candidate = offsets <= candidate_radius
        if np.count_nonzero(candidate) >= 8:
            selection = candidate
            radius = candidate_radius
            break
    if selection is None:
        return None
    sample = np.asarray(sizes, dtype=float)[selection]
    arcsec = float(np.median(sample))
    scales = np.asarray(
        proj_plane_pixel_scales(frame.wcs.celestial),
        dtype=float)*3600.
    if (not np.isfinite(scales).all() or np.min(scales) <= 0 or
            np.max(scales)/np.min(scales) > 1.05):
        raise ValueError(
            "Invalid native image WCS pixel scale for Tractor PSFSIZE")
    scale = float(np.mean(scales))
    fwhm = arcsec / scale
    if not 2.5 <= fwhm <= 20.:
        raise ValueError(
            f"Tractor PSFSIZE {arcsec:.3f} arcsec -> {fwhm:.3f} px "
            "outside conservative DAOStarFinder guess range")
    before = path.stat()
    digest = sha256()
    with path.open("rb") as reader:
        for block in iter(lambda: reader.read(4*1024*1024), b""):
            digest.update(block)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (
            after.st_size, after.st_mtime_ns):
        raise RuntimeError("Native Tractor seeing source changed during read")
    return {
        "available": True,
        "source": "LOCAL_DR11_TRACTOR_NEARBY_PSF_SIZE_NOT_PIXEL_MAP",
        "path": str(path),
        "source_sha256_local": digest.hexdigest(),
        "band": frame.band,
        "psfsize_arcsec": arcsec,
        "image_pixel_scale_arcsec": scale,
        "suggested_detection_fwhm_pix": fwhm,
        "sample": (
            f"median of {len(sample)} local BRICK_PRIMARY "
            f"PSFSIZE_{frame.band.upper()} entries within {radius:.0f} arcsec"),
        "n_tractor_psfsize_samples": int(len(sample)),
        "angular_radius_arcsec": radius,
        "is_measured_empirical_psf": False,
        "requires_researcher_review": True,
        "seeing_at_target_exactly_measured": False,
    }


def suggest_detection_fwhm_from_psfsize(frame, *, target_icrs_deg):
    """Try exact local coadd PSFSIZE map, then a *labeled* DR11 Tractor prior.

    An invalid map is never quietly accepted: report the rejected map
    and distinct fallback provenance. If neither product can provide
    a trustworthy seeing prior, keep the explicit manual FWHM guess.
    """
    missing_map = None
    map_error = None
    try:
        result = _suggest_detection_fwhm_from_native_map(
            frame, target_icrs_deg=target_icrs_deg)
    except (ValueError, OSError) as exc:
        map_error = exc
        missing_map = f"Native PSFSIZE map rejected: {exc}"
    else:
        if result["available"]:
            return result
        missing_map = result["message"]

    tractor = _suggest_from_native_tractor_psfsize(
        frame, target_icrs_deg=target_icrs_deg)
    if tractor is not None:
        tractor["native_map_status"] = missing_map
        return tractor
    if map_error is not None:
        raise ValueError(missing_map) from map_error
    return {
        "available": False,
        "source": "OPTIONAL_NATIVE_PSF_SIZE_MAP_ABSENT_NO_TRACTOR_FALLBACK",
        "message": (
            f"{missing_map}. No usable nearby same-band native Tractor "
            "PSFSIZE entries. Initial FWHM stays manual; verify the exact "
            "PSFSIZE file beside this project's native IMAGE FITS."),
    }
