"""Conservative DR11 Tractor/Gaia DR3 PSF-candidate evidence.

This is a source-identity gate, NOT proof of an uncontaminated star.
The DR11 Tractor REF_CAT='G3', REF_ID, TYPE, FITBITS and Gaia flags
come from the *same* release/region/brick as original imaging.
Image-centroid matching uses Tractor fitted RA/DEC (not raw Gaia
epoch=2016 catalog positions). Ambiguities are rejected, never forced.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import math
from pathlib import Path
import os
import tempfile
import urllib.request

import numpy as np
from scipy.spatial import cKDTree

from lsbpipe.surveys.legacy import ARCHIVE, BRICK_RE, regions_for_release


SCHEMA = "desidera.psf0-tractor-gaia-dr3/1"
MAX_CATALOG_BYTES = 350_000_000
# DR11 bitmasks: bright star, bailout, SGA, globular cluster and
# resolved dwarf galaxy. Current-band SATUR and ALLMASK also reject.
MASK_REJECT_BITS = (1 << 1) | (1 << 10) | (1 << 12) | (1 << 13) | (1 << 17)
FIT_REJECT_BITS = (1 << 0) | (1 << 4)  # forced PSF / frozen model
SATURATION_BITS = {"g": (2, 5), "r": (3, 6),
                   "z": (4, 7), "i": (14, 15)}
REQUIRED = {"BRICKNAME", "BRICK_PRIMARY", "RA", "DEC",
            "REF_CAT", "REF_ID", "TYPE", "OBJID", "FITBITS", "MASKBITS"}


def tractor_catalog_location(data_root, *, release, region, brick):
    if release != "dr11" or region != "south":
        raise ValueError("Gaia/Tractor PSF pilot is validated only for DR11 south")
    if region not in regions_for_release(release) or not BRICK_RE.fullmatch(brick):
        raise ValueError("Invalid Legacy Survey release/region/brick")
    path = (Path(data_root).resolve() / release / region / "tractor" /
            brick[:3] / f"tractor-{brick}.fits")
    return path, (f"{ARCHIVE}/{release}/{region}/tractor/{brick[:3]}/"
                  f"tractor-{brick}.fits")


def _hash_file(path):
    digest = sha256()
    with Path(path).open("rb") as file:
        for part in iter(lambda: file.read(4*1024*1024), b""):
            digest.update(part)
    return digest.hexdigest()


def _value(value):
    if isinstance(value, (bytes, np.bytes_)):
        return value.decode("ascii", errors="replace").strip()
    return str(value).strip()


def _number(value):
    try:
        out = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return out if math.isfinite(out) else None


@dataclass(frozen=True)
class TractorGaiaMatcher:
    """One versioned native-brick source catalogue, matched to image WCS."""
    source_path: str
    source_sha256: str
    dataset_id: str
    rows: tuple[dict, ...]
    pixel_positions: np.ndarray
    match_radius_pix: float = 2.5

    def __post_init__(self):
        points = np.asarray(self.pixel_positions, dtype=float)
        if (points.shape != (len(self.rows), 2) or
                not np.isfinite(points).all() or
                not .5 <= self.match_radius_pix <= 4.):
            raise ValueError("Invalid Tractor-to-image coordinate inventory")
        points = np.array(points, copy=True)
        points.setflags(write=False)
        object.__setattr__(self, "pixel_positions", points)
        object.__setattr__(self, "_tree", cKDTree(points))

    def match(self, x, y, *, band, neighbor_search_radius_pix=10.,
              require_gaia=True, return_audit=False):
        """Match a DAO detection to a clean Tractor PSF.

        In the exploratory Tractor-training mode Gaia identity is optional,
        but never fabricated. Every acceptance still requires the same
        model, masking, flux, ambiguity and neighbour gates. The optional
        structured trace records the FIRST failing gate and the available
        pre-failure evidence; it does not certify the stellar nature.
        """
        if band not in SATURATION_BITS:
            raise ValueError("Unknown optical band for Gaia screening")
        if type(require_gaia) is not bool:
            raise TypeError("require_gaia must be Boolean")
        trace = {
            "matched_tractor_objid": None,
            "tractor_ref_cat": None,
            "gaia_confirmed": False,
            "gaia_source_id": None,
            "tractor_type": None,
            "tractor_fitbits": None,
            "tractor_maskbits": None,
            "match_offset_pix": None,
            "nearest_tractor_centroid_distance_pix": None,
            "tractor_band_flux": None,
            "tractor_band_flux_ivar": None,
            "tractor_band_snr_diagonal_only": None,
            "nearest_catalog_neighbor_pix": None,
            "nearest_catalog_neighbor_any_distance_pix": None,
            "nearest_significant_neighbor_pix": None,
            "n_significant_neighbors": None,
            "checks_passed": [],
            "first_failure": None,
        }

        def finish(identity=None, failure=None):
            trace["first_failure"] = failure
            trace["outcome"] = "rejected" if failure else "catalog_accepted"
            if return_audit:
                return identity, failure, trace
            return identity, failure

        if not self.rows:
            return finish(failure="TRACTOR_NO_NATIVE_SOURCES")
        near = self._tree.query_ball_point(
            [float(x), float(y)], r=self.match_radius_pix)
        trace["n_catalog_matches"] = len(near)
        nearest_distance, _ = self._tree.query([float(x), float(y)], k=1)
        trace["nearest_tractor_centroid_distance_pix"] = float(nearest_distance)
        if len(near) == 0:
            return finish(failure="NO_TRACTOR_SOURCE_AT_DAO_CENTROID")
        if len(near) != 1:
            return finish(failure="AMBIGUOUS_TRACTOR_CROSSMATCH")
        index = int(near[0])
        row = self.rows[index]
        trace["checks_passed"].append("unique_tractor_centroid_match")
        trace.update({
            "matched_tractor_objid": row["objid"],
            "tractor_ref_cat": row["ref_cat"],
            "tractor_type": row["type"],
            "tractor_fitbits": row["fitbits"],
            "tractor_maskbits": row["maskbits"],
            "tractor_band_flux": row["flux"],
            "tractor_band_flux_ivar": row["flux_ivar"],
            "match_offset_pix": float(np.hypot(
                self.pixel_positions[index, 0]-x,
                self.pixel_positions[index, 1]-y)),
        })
        if len(self.rows) > 1:
            distances, _indices = self._tree.query(
                [float(x), float(y)], k=2)
            trace["nearest_catalog_neighbor_any_distance_pix"] = float(
                distances[1])
        if (row["flux"] is not None and row["flux_ivar"] is not None
                and row["flux"] > 0 and row["flux_ivar"] > 0):
            # Available even for a non-Gaia reference that fails the
            # strict identity check; never interpreted as calibrated S/N.
            trace["tractor_band_snr_diagonal_only"] = float(
                row["flux"] * math.sqrt(row["flux_ivar"]))
        is_gaia = row["ref_cat"] == "G3" and row["ref_id"] > 0
        trace["gaia_confirmed"] = is_gaia
        if is_gaia:
            trace["gaia_source_id"] = row["ref_id"]
            trace["checks_passed"].append("gaia_dr3_identity")
        elif require_gaia:
            return finish(failure="NOT_GAIA_DR3")
        if row["type"] != "PSF":
            return finish(failure="TRACTOR_NONSTELLAR_MODEL")
        trace["checks_passed"].append("tractor_psf_model")
        if row["fitbits"] & FIT_REJECT_BITS:
            return finish(failure="TRACTOR_FORCED_OR_FROZEN_PSF")
        trace["checks_passed"].append("not_forced_or_frozen")
        if row["maskbits"] & MASK_REJECT_BITS:
            return finish(failure="TRACTOR_BRIGHT_OR_UNSAFE_MASK")
        for bit in SATURATION_BITS[band]:
            if row["maskbits"] & (1 << bit):
                return finish(failure="TRACTOR_SATURATED_OR_ALLMASK")
        trace["checks_passed"].append("safe_catalog_masks")
        if row["gaia_duplicated_source"]:
            return finish(failure="GAIA_DUPLICATED_SOURCE")
        trace["checks_passed"].append("not_gaia_duplicate")
        if (row["flux_ivar"] is None or row["flux_ivar"] <= 0 or
                row["flux"] is None or row["flux"] <= 0):
            return finish(failure="TRACTOR_NO_FITTED_BAND_FLUX")
        snr = row["flux"]*math.sqrt(row["flux_ivar"])
        trace["tractor_band_snr_diagonal_only"] = float(snr)
        if snr < 20.:
            return finish(failure="TRACTOR_LOW_BAND_SIGNAL_TO_NOISE")
        trace["checks_passed"].append("tractor_diagonal_snr_gte_20")
        radius = float(neighbor_search_radius_pix)
        if not 2 <= radius <= 50 or not math.isfinite(radius):
            raise ValueError("Invalid Tractor neighbor-search aperture")
        neighbors = self._tree.query_ball_point(
            [float(x), float(y)], r=radius)
        separations = sorted(
            float(np.hypot(self.pixel_positions[int(j), 0]-x,
                           self.pixel_positions[int(j), 1]-y))
            for j in neighbors if int(j) != index)
        trace["nearest_catalog_neighbor_pix"] = (
            separations[0] if separations else None)
        significant = []
        for j in neighbors:
            if int(j) == index:
                continue
            other = self.rows[int(j)]
            flux, ivar = other.get("flux"), other.get("flux_ivar")
            if (flux is None or ivar is None or
                    flux <= .02*row["flux"] or ivar <= 0):
                continue
            if flux*math.sqrt(ivar) >= 5.:
                significant.append(float(np.hypot(
                    self.pixel_positions[int(j), 0]-x,
                    self.pixel_positions[int(j), 1]-y)))
        trace["n_significant_neighbors"] = len(significant)
        trace["nearest_significant_neighbor_pix"] = (
            min(significant) if significant else None)
        if significant:
            return finish(failure="TRACTOR_NEIGHBOR_WITHIN_PSF_CUTOUT")
        trace["checks_passed"].append("no_significant_tractor_neighbors")
        return finish(identity={
            "catalog": "DESI_DR11_TRACTOR_GAIA_DR3" if is_gaia
                       else "DESI_DR11_TRACTOR_PSF_UNVERIFIED_GAIA",
            "gaia_source_id": row["ref_id"] if is_gaia else None,
            "gaia_confirmed": is_gaia,
            "tractor_ref_cat": row["ref_cat"],
            "tractor_objid": row["objid"],
            "tractor_type": row["type"],
            "match_offset_pix": trace["match_offset_pix"],
            "gaia_g_mag": row["gaia_g_mag"] if is_gaia else None,
            "tractor_band_snr_diagonal_only": float(snr),
            "catalog_sha256_local": self.source_sha256,
            "is_clean_psf_proven": False,
        })


def read_native_tractor_gaia(data_root, *, release, region, brick,
                             frame, max_bytes=MAX_CATALOG_BYTES):
    """Validate and read the exact versioned local native Tractor FITS.

    The input is always the *original* Tractor catalog; it is not Gaia
    queried at different epoch or an unverified viewer overlay.
    """
    from astropy.io import fits
    path, _url = tractor_catalog_location(
        data_root, release=release, region=region, brick=brick)
    if not path.is_file() or path.is_symlink():
        raise FileNotFoundError(
            f"DR11 Tractor Gaia catalogue missing: {path}. "
            "Use Get DR11 Tractor+Gaia in the Photometry GUI.")
    before = path.stat()
    if not 200 < before.st_size <= max_bytes:
        raise ValueError("Tractor FITS outside bounded local size range")
    if frame.dataset_id != f"{release}/{region}/{brick}/{frame.band}":
        raise ValueError("Tractor catalogue and native IMAGE dataset mismatch")
    with fits.open(path, memmap=True) as hdus:
        tables = [h for h in hdus if isinstance(h, fits.BinTableHDU)]
        if len(tables) != 1:
            raise ValueError("Expected exactly one native Tractor FITS table")
        data = tables[0].data
        names = {name.upper():name for name in tables[0].columns.names}
        missing = REQUIRED - set(names)
        required_band = "FLUX_IVAR_" + frame.band.upper()
        flux_band = "FLUX_" + frame.band.upper()
        missing |= {"GAIA_DUPLICATED_SOURCE", required_band,
                    flux_band} - set(names)
        if missing:
            raise ValueError(f"Tractor Gaia fields missing: {sorted(missing)}")
        if len(data) > 500_000:
            raise ValueError("Tractor table is unexpectedly large")
        catalog_rows = []
        world = []
        flux_key = names[required_band]
        signal_key = names[flux_band]
        for row in data:
            if _value(row[names["BRICKNAME"]]) != brick:
                raise ValueError("Tractor FITS contains another brick identity")
            ra = _number(row[names["RA"]])
            dec = _number(row[names["DEC"]])
            if (ra is None or dec is None or
                    not 0 <= ra < 360 or not -90 <= dec <= 90 or
                    not bool(row[names["BRICK_PRIMARY"]])):
                continue
            world.append((ra,dec))
            ref_id = int(row[names["REF_ID"]])
            catalog_rows.append({
                "ref_cat": _value(row[names["REF_CAT"]]).upper(),
                "ref_id": ref_id,
                "objid": int(row[names["OBJID"]]),
                "type": _value(row[names["TYPE"]]).upper(),
                "fitbits": int(row[names["FITBITS"]]),
                "maskbits": int(row[names["MASKBITS"]]),
                "flux": _number(row[signal_key]),
                "flux_ivar": _number(row[flux_key]),
                "gaia_duplicated_source": bool(
                    row[names["GAIA_DUPLICATED_SOURCE"]]),
                "gaia_g_mag": (
                    _number(row[names["GAIA_PHOT_G_MEAN_MAG"]])
                    if "GAIA_PHOT_G_MEAN_MAG" in names else None),
                # P15: retain DR11 physical/shape metadata only when
                # actually present in the original SAME brick FITS.
                # SHAPE_R is in arcsec; e1/e2 are Tractor's reduced
                # ellipticity components, not sky pixel axis ratios.
                # None MUST NOT be replaced with a guessed radius.
                "shape_r_arcsec": (
                    _number(row[names["SHAPE_R"]])
                    if "SHAPE_R" in names else None),
                "shape_e1": (
                    _number(row[names["SHAPE_E1"]])
                    if "SHAPE_E1" in names else None),
                "shape_e2": (
                    _number(row[names["SHAPE_E2"]])
                    if "SHAPE_E2" in names else None),
                "sersic_n": (
                    _number(row[names["SERSIC"]])
                    if "SERSIC" in names else None),
                # Formal per-parameter inverse variances supplied by
                # Tractor, NOT a joint covariance matrix. Never use
                # them as calibrated perturbation amplitudes.
                "shape_r_ivar_arcsec_m2": (
                    _number(row[names["SHAPE_R_IVAR"]])
                    if "SHAPE_R_IVAR" in names else None),
                "shape_e1_ivar": (
                    _number(row[names["SHAPE_E1_IVAR"]])
                    if "SHAPE_E1_IVAR" in names else None),
                "shape_e2_ivar": (
                    _number(row[names["SHAPE_E2_IVAR"]])
                    if "SHAPE_E2_IVAR" in names else None),
                "morphology_fields_in_native_catalog": {
                    key: key in names for key in (
                        "SHAPE_R", "SHAPE_E1", "SHAPE_E2", "SERSIC",
                        "SHAPE_R_IVAR", "SHAPE_E1_IVAR", "SHAPE_E2_IVAR")
                },
            })
    digest = _hash_file(path)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (
            after.st_size, after.st_mtime_ns):
        raise RuntimeError("Tractor FITS changed during source-identity verification")
    if world:
        lon, lat = frame.wcs.all_world2pix(
            np.array([q[0] for q in world]),
            np.array([q[1] for q in world]), 0)
        coords = np.column_stack((lon, lat))
        ok = np.isfinite(coords).all(axis=1)
        coords = coords[ok]
        catalog_rows = [r for r, keep in zip(catalog_rows, ok) if keep]
    else:
        coords = np.zeros((0,2))
    return TractorGaiaMatcher(
        source_path=str(path),
        source_sha256=digest,
        dataset_id=frame.dataset_id,
        rows=tuple(catalog_rows), pixel_positions=coords)


def download_native_tractor_gaia(data_root, *, release, region, brick,
                                 progress=None):
    """Explicit GUI-only archive fetch, atomically saved under current root.

    No source quartet is changed. Never overwrite any existing file;
    validate checksum, binary table identity and schema before acceptance
    by the science pipeline.
    """
    from astropy.io import fits
    target, url = tractor_catalog_location(
        data_root, release=release, region=region, brick=brick)
    if target.is_file() and not target.is_symlink():
        return {"status":"ALREADY_PRESENT_UNVERIFIED",
                "path":str(target), "url":url}
    if target.exists() or target.is_symlink():
        raise ValueError("Unsafe or non-file destination for Tractor catalogue")
    root = Path(data_root).resolve()
    for parent in (target.parent, *target.parents):
        if parent == root.parent:
            break
        if parent.is_symlink():
            raise ValueError("Symlinked source directory prohibited")
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmpname = tempfile.mkstemp(prefix=".tractor-partial-", dir=target.parent)
    total = 0
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url, headers={
                    "User-Agent":"Desidera-PSF0-Gaia-Catalog/1"}),
                timeout=45) as response, os.fdopen(fd, "wb") as writer:
            fd = -1
            if not response.geturl().startswith(ARCHIVE + "/"):
                raise ValueError("Tractor archive redirected outside trusted host")
            while True:
                if progress is not None:
                    checkpoint = getattr(progress, "checkpoint", None)
                    if checkpoint is not None:
                        checkpoint()
                block = response.read(1024*1024)
                if not block:
                    break
                total += len(block)
                if total > MAX_CATALOG_BYTES:
                    raise ValueError("Tractor file download exceeds 350 MB limit")
                writer.write(block)
            writer.flush()
            os.fsync(writer.fileno())
        if total <= 200:
            raise ValueError("Archive delivered an empty/non-FITS Tractor file")
        with fits.open(tmpname, memmap=True) as hdus:
            tables = [h for h in hdus if isinstance(h, fits.BinTableHDU)]
            if len(tables) != 1:
                raise ValueError("Downloaded Tractor is not one binary table")
            names = {n.upper() for n in tables[0].columns.names}
            if (REQUIRED - names or "GAIA_DUPLICATED_SOURCE" not in names or
                    "FLUX_G" not in names or "FLUX_IVAR_G" not in names):
                raise ValueError("Downloaded Tractor FITS lacks Gaia/flux QA fields")
            data = tables[0].data
            if len(data) and _value(data["BRICKNAME"][0]) != brick:
                raise ValueError("Downloaded Tractor FITS brick identity mismatches")
        # Same-directory atomic hard link is exclusive: unlike os.replace,
        # it will NEVER overwrite an original catalogue created meanwhile.
        # The temporary inode is removed by the finally clause only after
        # the final path is safely linked.
        os.link(tmpname, target)
        return {"status":"ORIGINAL_TRACTOR_DOWNLOADED_NOT_SCIENCE_ADOPTED",
                "path":str(target), "url":url,
                "sha256_local":_hash_file(target),
                "size_bytes":total}
    finally:
        if fd >= 0:
            os.close(fd)
        Path(tmpname).unlink(missing_ok=True)
