"""PSF-0: experimental, independently held-out empirical *coadd* PSF.

Operates exclusively on an existing NativeBandFrame (strict original FITS
adapter), not a remote preview. Native pixels are never written or changed.
Local stellar cutout sky is distinct from the LSB galaxy sky solution.

No measurement here is an adopted, spatially validated, wide-angle or
SBF-grade PSF. In particular, small-cutout EPSFs do not constrain wings.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
from collections.abc import Mapping
from hashlib import sha256
import json
import math

import numpy as np
from scipy.spatial import cKDTree

from lsbpipe.photometry.contracts import NativeBandFrame
from lsbpipe.photometry.workflow_dependencies import PSFLineageKey
from lsbpipe.photometry.psf_signed_qa import signed_psf_qa
from lsbpipe.photometry.psf_holdout_qa import (
    heldout_signed_residual, heldout_final_kernel_residual,
    summarize_signed_holdouts, summarize_core_normalized_growth,
    audit_signed_holdout_coverage,
)
from lsbpipe.photometry.psf_star_quality import (
    assess_psf_star_cutout, assess_sample_widths,
)
from lsbpipe.photometry.psf_training_residuals import (
    training_star_residual, choose_training_outliers, brightness_bias_audit,
)
from lsbpipe.photometry.psf_parameter_audit import (
    audit_psf0_parameters, audit_star_sky_ring_sensitivity,
    audit_gaia_catalog_seeds_vs_dao,
)
from lsbpipe.photometry.psf_joint_scene import audit_fixed_psf_joint_scenes
from lsbpipe.sky_geometry import ellipse_from_wcs

STATUS = "EXPERIMENTAL_PSF_NOT_ADOPTED"
RECIPE = "desidera.psf0-coadd-epsf/2"


class PSF0QualityError(ValueError):
    """Scientifically rejected ePSF, with read-only candidate-star QA evidence."""

    def __init__(self, message: str, diagnostics: dict, review_snapshot=None):
        super().__init__(message)
        self.diagnostics = diagnostics
        self.review_snapshot = review_snapshot


@dataclass(frozen=True)
class PSF0Config:
    """Reproducible, conservative core-PSF recipe (not a science calibration)."""
    guessed_fwhm_pix: float = 4.8
    detection_sigma: float = 7.
    cutout_size_pix: int = 31
    sky_annulus_inner_pix: float = 10.
    sky_annulus_outer_pix: float = 15.
    max_invalid_fraction: float = .01
    min_neighbor_separation_pix: float = 32.
    min_train_stars: int = 10
    min_holdout_stars: int = 3
    holdout_every: int = 5
    max_training_stars: int = 75
    max_iterations: int = 8
    wing_smoothing: bool = True
    psf_oversampling: int = 1
    max_star_gradient_sigma: float = 6.
    seed: int = 20261103
    # Reviewed identifiers are fixed before model fitting. Holdouts cannot
    # be manually removed to improve the QA score.
    excluded_training_star_ids: tuple[str, ...] = ()
    # DAO fallback is explicitly diagnostic; strict Gaia mode needs a
    # verified, release-matched, locally saved original Tractor table.
    star_catalog_policy: str = "dao_diagnostic"
    # Only when explicitly requested. Star-level trimming is separate
    # from photutils pixelwise sigma-clipping and needs heldout QA.
    training_outlier_passes: int = 0

    def __post_init__(self):
        floats = (self.guessed_fwhm_pix, self.detection_sigma,
                  self.sky_annulus_inner_pix, self.sky_annulus_outer_pix,
                  self.max_invalid_fraction, self.min_neighbor_separation_pix,
                  self.max_star_gradient_sigma)
        if not all(math.isfinite(x) for x in floats):
            raise ValueError("Nonfinite PSF recipe value")
        if not 2.5 <= self.guessed_fwhm_pix <= 20:
            raise ValueError("PSF-0 requires sampled stars with guessed FWHM >=2.5 px")
        if not 3 <= self.detection_sigma <= 30:
            raise ValueError("Detection threshold out of conservative range")
        if type(self.cutout_size_pix) is not int or not 15 <= self.cutout_size_pix <= 91 or self.cutout_size_pix % 2 != 1:
            raise ValueError("Star cutout must be odd and 15..91 px")
        h = self.cutout_size_pix // 2
        if not 2 < self.sky_annulus_inner_pix < self.sky_annulus_outer_pix <= h:
            raise ValueError("Local stellar sky annulus must lie inside cutout")
        if not 0 <= self.max_invalid_fraction <= .1 or self.min_neighbor_separation_pix < self.cutout_size_pix:
            raise ValueError("Invalid pixel fraction or unsafe neighbor isolation")
        if (type(self.min_train_stars) is not int or self.min_train_stars < 4 or
                type(self.min_holdout_stars) is not int or self.min_holdout_stars < 2 or
                type(self.holdout_every) is not int or self.holdout_every < 3 or
                type(self.max_training_stars) is not int or
                self.max_training_stars < self.min_train_stars or
                type(self.max_iterations) is not int or not 2 <= self.max_iterations <= 25 or
                type(self.seed) is not int):
            raise ValueError("Invalid deterministic star split/build settings")
        if type(self.wing_smoothing) is not bool:
            raise ValueError("Experimental wing smoothing policy must be Boolean")
        if self.psf_oversampling != 1:
            raise ValueError("PSF-0 currently supports only a native-grid EPSF; oversampling requires separate calibration")
        if self.max_star_gradient_sigma <= 0:
            raise ValueError("Gradient rejection threshold must be positive")
        if self.star_catalog_policy not in (
                "dao_diagnostic", "gaia_strict", "tractor_psf_validated"):
            raise ValueError("Unknown PSF star-catalogue selection policy")
        if type(self.training_outlier_passes) is not int or not (
                0 <= self.training_outlier_passes <= 2):
            raise ValueError("Training-star residual screening permits 0–2 passes")
        excluded = self.excluded_training_star_ids
        if (not isinstance(excluded, tuple) or
                any(not isinstance(key, str) or
                    re.fullmatch("star-[0-9]{4}", key) is None for key in excluded) or
                len(set(excluded)) != len(excluded) or
                excluded != tuple(sorted(excluded))):
            raise ValueError(
                "Manual training-star exclusions must be sorted, unique star-XXXX IDs")

    @property
    def digest(self):
        return sha256(json.dumps(asdict(self), sort_keys=True,
                                 allow_nan=False).encode("utf8")).hexdigest()


@dataclass(frozen=True)
class PSF0Star:
    """Audit record: selection, background, and sky coordinates of a star."""
    star_id: str
    x_pix: float
    y_pix: float
    ra_deg: float
    dec_deg: float
    peak_nMgy: float
    cutout_sky_nMgy: float
    cutout_rms_nMgy: float
    gradient_sigma_span: float
    role: str
    gaia_source_id: int | None = None
    gaia_g_mag: float | None = None
    image_axis_ratio: float | None = None
    image_core_sigma_pix: float | None = None

    def __post_init__(self):
        if self.role not in ("train", "holdout"):
            raise ValueError("Star role must be declared")


def _immutable_kernel(value):
    arr = np.asarray(value, dtype=np.float64)
    if (arr.ndim != 2 or min(arr.shape) < 15 or
            any(n % 2 != 1 for n in arr.shape) or
            not np.all(np.isfinite(arr)) or np.any(arr < 0) or
            not np.isclose(float(arr.sum()), 1., atol=1e-9, rtol=1e-8)):
        raise ValueError("Experimental PSF kernel must be finite, nonnegative, odd and normalized")
    return np.frombuffer(arr.tobytes(order="C"), dtype=np.float64).reshape(arr.shape)


@dataclass(frozen=True)
class PSF0Result:
    dataset_id: str
    instrument: str
    kernel: np.ndarray = field(repr=False, compare=False)
    training: tuple[PSF0Star, ...]
    holdout: tuple[PSF0Star, ...]
    rejected_reasons: dict
    detection_count: int
    metrics: dict
    flags: tuple[str, ...]
    recipe: PSF0Config
    source_hashes: dict
    lineage_id: str | None
    center_target_icrs: tuple[float, float]
    target_protection_arcsec: float
    algorithm_versions: dict
    review_snapshot: dict | None = field(default=None, repr=False, compare=False)
    status: str = STATUS

    def __post_init__(self):
        if self.status != STATUS:
            raise ValueError("PSF-0 cannot produce adopted PSF results")
        object.__setattr__(self, "kernel", _immutable_kernel(self.kernel))
        object.__setattr__(self, "training", tuple(self.training))
        object.__setattr__(self, "holdout", tuple(self.holdout))
        object.__setattr__(self, "flags", tuple(self.flags))
        if (set(s.star_id for s in self.training) &
                set(s.star_id for s in self.holdout)):
            raise ValueError("PSF train/holdout contamination")
        if len(self.training) < self.recipe.min_train_stars or len(self.holdout) < self.recipe.min_holdout_stars:
            raise ValueError("PSF lacks disjoint holdout/training stars")
        if self.lineage_id is not None and (
                len(self.lineage_id) != 64 or "SYNTHETIC_TEST_INPUT" in self.flags):
            raise ValueError("Invalid science provenance for PSF lineage")

    def report(self):
        return {
            "schema": "desidera.psf0-experimental/1",
            "status": self.status,
            "science_adopted": False,
            "dataset_id": self.dataset_id, "instrument": self.instrument,
            "kernel_shape": list(self.kernel.shape),
            "kernel_native_pixel_sampling": True,
            "kernel_sum": float(self.kernel.sum()),
            "kernel_sha256": sha256(self.kernel.tobytes(order="C")).hexdigest(),
            "source_hashes": dict(self.source_hashes),
            "lineage_id": self.lineage_id,
            "recipe": asdict(self.recipe),
            "recipe_digest": self.recipe.digest,
            "implementation_method_sha256": self.metrics.get(
                "implementation_method_sha256"),
            "target_icrs_deg": list(self.center_target_icrs),
            "target_protection_arcsec": self.target_protection_arcsec,
            "n_detected": self.detection_count,
            "n_train": len(self.training), "n_holdout": len(self.holdout),
            "manual_excluded_training_star_ids": list(
                self.recipe.excluded_training_star_ids),
            "training": [asdict(s) for s in self.training],
            "holdout": [asdict(s) for s in self.holdout],
            "rejections": dict(self.rejected_reasons),
            "diagnostic_metrics": dict(self.metrics),
            "flags": list(self.flags),
            "algorithm_versions": dict(self.algorithm_versions),
            "psf_wings_validated": False,
            "psf_spatial_representativity_validated": False,
            "noise_covariance_validated": False,
            "stellar_contamination_model_validated": False,
        }


def _implementation_method_fingerprint(cfg, kwargs, versions,
                                        solver_diagnostics, recenter_name):
    """Bind source-independent PSF lineage to effective software and defaults."""
    payload = {
        "schema": "desidera.psf0-implementation-method/1",
        "contract": RECIPE, "config_digest": cfg.digest,
        "builder_kwargs": dict(kwargs), "package_versions": dict(versions),
        "fit_shape": solver_diagnostics.get("fit_shape_final_if_reported"),
        "smoothing": solver_diagnostics.get(
            "smoothing_kernel_final_if_reported"),
        "recentering": recenter_name,
        "convergence_fraction": solver_diagnostics.get(
            "convergence_fraction_target_if_reported"),
        "holdout_aperture": "NATIVE_SUBPIXEL_STAR_CENTER_V2",
        "holdout_integrity": "ALL_FIXED_STARS_ALL_ANNULI",
        "holdout_flux_growth": "CORE_NORM_NATIVE_APERTURE_RADII_V1_DESCRIPTIVE",
        "parameter_provenance": "PILOT_NUMERIC_ASSUMPTION_REGISTRY_V1_DESCRIPTIVE",
        "stellar_sky_ring_sensitivity": "ORIGINAL_PIXELS_INTERIOR_RING_AND_NATIVE_SCAN_V2_DESCRIPTIVE",
        "gaia_seed_dao_coverage": "CATALOG_PREFLIGHT_V1_NO_SAMPLE_PROMOTION",
        "holdout_sky_equivalence": "CORE_NORMALIZED_UNIFORM_OFFSET_WITH_RADIAL_CONSISTENCY_V2",
        "fixed_psf_joint_native_scene": "STAR_POINT_NEIGHBORS_PLUS_PLANAR_SKY_V3_NONCAUSAL_RSS_ALGEBRA_QA_ONLY",
        "original_tractor_shape_ingestion": "NATIVE_SHAPE_R_ARCSEC_E1_E2_SERSIC_PLUS_OPTIONAL_SHAPE_IVARS_FAIL_CLOSED_P15D",
        "approximate_extended_tractor_scene": "REX_EXP_DEV_SERSIC_N1_N4_SUBPIXEL3_FIXED_PSF_NATIVE_P15_V2_RING_HELDOUT_QA_ONLY",
        "p15_reserved_ring_pixel_prediction": "SAME_ORIGINAL_NATIVE_PIXELS_EXCLUDE_2_TO_4_GUESSED_FWHM_FROM_BOTH_FITS_V1",
        "p15d_morphology_stress": "NEAREST_RENDERED_TRACTOR_EXTENDED_OAT_RADIUS10PCT_ELLIPTICITY0P05_PA10DEG_FIXED_RING_V1_QA_ONLY",
        "p15e_profile_sampling_convergence": "ANALYTIC_SERSIC_SAME_NATIVE_FINITE_PSF_GRID3_5_7_NOT_EXACT_TRACTOR_V1",
        "p15e_external_reference_gate": "EXPLICIT_MATCHED_TRACTOR_STAMP_REQUIRED_NO_INVENTED_EXTERNAL_REFERENCE_V1",
    }
    return {
        "sha256": sha256(json.dumps(
            payload, sort_keys=True, separators=(",", ":"),
            allow_nan=False).encode("utf8")).hexdigest(),
        "payload": payload,
    }


def _verified_source_hashes(frame):
    info = frame.source_evidence
    hashes = info.get("local_hashes", {})
    if (info.get("hash_provenance") != "LOCAL_SHA256_NOT_REMOTE_AUTHENTICITY"
            or info.get("synthetic", False) or not isinstance(hashes, Mapping)
            or set(hashes) != {"image", "invvar", "nexp", "maskbits"}
            or any(not isinstance(v, str) or len(v) != 64 or
                   any(char not in "0123456789abcdef" for char in v)
                   for v in hashes.values())):
        return None
    return dict(hashes)


def _target_exclusion(frame, center_icrs, radius_arcsec):
    if (len(center_icrs) != 2 or not all(math.isfinite(float(c)) for c in center_icrs)
            or not math.isfinite(radius_arcsec) or not 0 < radius_arcsec <= 1800):
        raise ValueError("Target ICRS and conservative protection radius required")
    ra, dec = map(float, center_icrs)
    if not 0 <= ra < 360 or not -90 <= dec <= 90:
        raise ValueError("Invalid ICRS coordinates")
    ellipse = ellipse_from_wcs(frame.wcs, ra, dec, sky_q=1., sky_pa_deg=0.)
    ny, nx = frame.shape
    yy, xx = np.indices((ny, nx))
    # Local-affine approximation for one survey brick/cutout only, not
    # a spherical sky polygon. Guard this limitation via flags.
    return ellipse.pixel.elliptical_radius(xx, yy) < radius_arcsec * ellipse.pixel_a_per_sky_arcsec


def _star_cutout(frame, x, y, cfg, protected):
    """Fit a local planar background on the annulus of this one star."""
    h = cfg.cutout_size_pix // 2
    ix, iy = int(np.rint(x)), int(np.rint(y))
    ny, nx = frame.shape
    if ix-h < 0 or iy-h < 0 or ix+h >= nx or iy+h >= ny:
        return None, "CUTOUT_EDGE"
    rows = slice(iy-h, iy+h+1)
    cols = slice(ix-h, ix+h+1)
    valid = np.array(frame.valid[rows, cols], dtype=bool, copy=True)
    for key in (f"SATUR_{frame.band.upper()}", f"ALLMASK_{frame.band.upper()}"):
        if key in frame.mask_reasons:
            valid &= ~frame.mask_reasons[key][rows, cols]
    valid &= ~protected[rows, cols]
    if (not valid[h, h] or
            np.count_nonzero(~valid) / valid.size > cfg.max_invalid_fraction):
        return None, "PIXEL_QUALITY_OR_TARGET_OVERLAP"
    src = np.asarray(frame.image_nMgy[rows, cols], dtype=float)
    ivar = np.asarray(frame.invvar_nMgy2[rows, cols], dtype=float)
    gy, gx = np.indices(src.shape)
    dx, dy = gx-(x-(ix-h)), gy-(y-(iy-h))
    rr = np.hypot(dx, dy)
    # A bright star may have one or two invalid pixels inside the
    # PSF-fitting core while the entire 51x51 cutout still passes the
    # <=1% masked-area heuristic. The original check only inspected
    # the nearest INTEGER central pixel. Leo P's real star-0003 showed
    # two ochre-masked pixels near the subpixel centroid in the GUI.
    # Reject a star with ANY invalid native pixel inside one assumed
    # FWHM, even if the cutout-wide bad fraction is tiny. It must not
    # contribute a damaged core to the ePSF sample or holdout.
    if np.any((~valid) & (rr <= cfg.guessed_fwhm_pix)):
        return None, "MASKED_STELLAR_PSF_CORE_PIXELS"
    annulus = (valid & (rr >= cfg.sky_annulus_inner_pix)
               & (rr < cfg.sky_annulus_outer_pix))
    if np.count_nonzero(annulus) < 35:
        return None, "INSUFFICIENT_LOCAL_SKY"
    # Three robust least-squares iterations, independent of the LSB
    # galaxy-wide background. Even accepted fit is PSF-wing sensitive.
    design = np.column_stack((np.ones(np.count_nonzero(annulus)),
                              dx[annulus], dy[annulus]))
    observed = src[annulus]
    include = np.ones(len(observed), dtype=bool)
    for _ in range(4):
        if include.sum() < 25:
            return None, "INSUFFICIENT_LOCAL_SKY"
        coeff = np.linalg.lstsq(design[include], observed[include], rcond=None)[0]
        resid = observed - design @ coeff
        scale = 1.4826 * np.median(np.abs(resid[include] - np.median(resid[include])))
        if not np.isfinite(scale) or scale <= 0:
            scale = np.std(resid[include])
        if not np.isfinite(scale) or scale <= 0:
            return None, "NO_LOCAL_NOISE_ESTIMATE"
        include = np.abs(resid-np.median(resid[include])) < 3.5*scale
    if np.count_nonzero(include) < 25:
        return None, "INSUFFICIENT_LOCAL_SKY"
    # Refit after the last rejection; the previous iteration's
    # coefficients otherwise describe a *different* pixel subset.
    coeff = np.linalg.lstsq(design[include], observed[include], rcond=None)[0]
    full_sky = coeff[0] + coeff[1]*dx + coeff[2]*dy
    residual = src - full_sky
    core = (rr <= max(2.5, cfg.guessed_fwhm_pix))
    star_peak = float(np.max(residual[valid & core])) if np.any(valid & core) else float("nan")
    if not np.isfinite(star_peak) or star_peak <= cfg.detection_sigma*scale:
        return None, "LOW_LOCAL_STAR_CONTRAST"
    gradient_span = float(np.hypot(coeff[1], coeff[2]) * 2*h / scale)
    # Independent diagnostics on the annular plane fit, not a calibrated
    # deblended stellar halo/sky separation. For example, at 4.8 px
    # FWHM the baseline ring 10–15 px *must* be flagged as wing risk.
    mid = .5*(cfg.sky_annulus_inner_pix+cfg.sky_annulus_outer_pix)
    low_ring = annulus & (rr < mid)
    high_ring = annulus & (rr >= mid)
    radial_trend_sigma = (
        float((np.median(residual[low_ring]) -
               np.median(residual[high_ring])) / scale)
        if min(np.count_nonzero(low_ring),
               np.count_nonzero(high_ring)) >= 10 else None)
    angle = np.mod(np.arctan2(dy,dx), 2*np.pi)
    sector_medians = []
    for j in range(8):
        sector = annulus & (angle >= j*np.pi/4) & (
            angle < (j+1)*np.pi/4)
        if np.count_nonzero(sector) >= 5:
            sector_medians.append(float(np.median(residual[sector])))
    sector_scatter_sigma = (
        float(1.4826*np.median(np.abs(
            np.asarray(sector_medians)-np.median(sector_medians))) / scale)
        if len(sector_medians) >= 6 else None)
    sky_qa = {
        "method":"ROBUST_LOCAL_PLANE_ON_STELLAR_ANNULUS",
        "annulus_inner_in_guessed_fwhm":
            float(cfg.sky_annulus_inner_pix/cfg.guessed_fwhm_pix),
        "annulus_outer_in_guessed_fwhm":
            float(cfg.sky_annulus_outer_pix/cfg.guessed_fwhm_pix),
        "wing_sky_degeneracy_warning": (
            cfg.sky_annulus_inner_pix < 4*cfg.guessed_fwhm_pix),
        "annulus_sigma_clip_retained_fraction":
            float(np.count_nonzero(include)/len(observed)),
        "inner_minus_outer_residual_median_over_noise":
            radial_trend_sigma,
        "octant_median_spread_over_noise":sector_scatter_sigma,
        "sky_uncertainty_covariance_calibrated":False,
        "sky_versus_psf_wings_separable_from_this_cutout":False,
    }
    # Independent observational sensitivity: re-fit *original* native
    # pixels in predeclared interior annuli. This never changes the chosen
    # sky plane, training samples, holdouts or acceptance thresholds.
    sky_qa["inner_ring_sensitivity_diagnostic"] = (
        audit_star_sky_ring_sensitivity(
            src, valid, center_xy=(x-(ix-h), y-(iy-h)),
            inner_radius=cfg.sky_annulus_inner_pix,
            outer_radius=cfg.sky_annulus_outer_pix,
            reference_sky_nMgy_pix=float(coeff[0]),
            reference_rms_nMgy_pix=float(scale),
            guessed_fwhm_pix=cfg.guessed_fwhm_pix))
    if gradient_span > cfg.max_star_gradient_sigma:
        return None, "STRONG_LOCAL_SKY_GRADIENT"
    # A nonzero masked cutout cannot silently become a reconstructed
    # original pixel: those samples receive zero statistical weight.
    cutout = np.where(valid, residual, 0.)
    weights = np.where(valid, np.sqrt(np.maximum(ivar, 0.)), 0.)
    if not np.isfinite(weights).all():
        return None, "BAD_CUTOUT_WEIGHTS"
    return {
        "image": cutout, "weights": weights,
        "center_in_cutout": (x-(ix-h), y-(iy-h)),
        "sky_nMgy": float(coeff[0]), "rms_nMgy": float(scale),
        "gradient_sigma_span": gradient_span,
        "sky_qa":sky_qa,
        "peak_nMgy": star_peak,
        "valid": valid,
    }, None


def _holdout_diagnostic(star, cutout, kernel):
    """Native-pixel weighted flux fit: diagnostic only, not calibrated chi^2."""
    from scipy.ndimage import shift
    cy, cx = (np.array(kernel.shape)-1)/2.
    local_x, local_y = cutout["center_in_cutout"]
    model = shift(kernel, (local_y-cy, local_x-cx),
                  order=3, mode="constant", cval=0., prefilter=True)
    usable = cutout["weights"] > 0
    # Exclude any interpolation ringing: tiny signed numerical artifacts
    # outside the model support are *not* interpreted as stellar flux.
    model = np.maximum(model, 0.)
    numerator = float(np.sum(cutout["weights"][usable]**2 *
                             model[usable] * cutout["image"][usable]))
    denominator = float(np.sum(cutout["weights"][usable]**2 * model[usable]**2))
    if denominator <= 0 or not math.isfinite(denominator):
        raise ValueError("Holdout PSF normalization is undefined")
    flux = numerator/denominator
    if flux <= 0 or not math.isfinite(flux):
        raise ValueError("Holdout star fitted flux is invalid")
    err = cutout["image"]-flux*model
    inner = np.indices(kernel.shape)
    # The model was shifted to this native subpixel location; the
    # validation aperture MUST use the same stellar center, not the
    # unshifted central pixel of the kernel array.
    r = np.hypot(inner[1]-local_x, inner[0]-local_y)
    core_radius = min(cx, cy, 9.)
    local = usable & (r <= core_radius)
    if not np.any(local):
        raise ValueError("Empty PSF holdout aperture")
    return {
        "source_id": star.star_id,
        "relative_core_abs_residual": float(np.median(np.abs(err[local])) /
                                            max(cutout["peak_nMgy"], 1e-12)),
        "flux_nMgy_diagnostic": float(flux),
        "background_rms_nMgy": cutout["rms_nMgy"],
        "core_aperture_center_native_xy": [float(local_x), float(local_y)],
        "core_aperture_radius_pix": float(core_radius),
        "n_core_aperture_pixels": int(np.count_nonzero(local)),
        "subpixel_center_consistent_with_model_shift": True,
    }


def _identity_population_qa(entries):
    """Descriptive brightness/shape comparison; NEVER alters star selection.

    The native positive-core moments are biased for faint cutouts and are
    not an independent star/galaxy classifier. Compare Gaia vs unverified
    Tractor PSF detections only in explicitly recorded peak-flux strata.
    """
    groups = {"gaia_dr3": [], "tractor_psf_without_gaia": []}
    for entry in entries:
        cut = entry[-1]
        identity = cut.get("star_identity")
        shape = cut.get("shape_qa") or {}
        peak = float(cut.get("peak_nMgy", 0.))
        sigma = shape.get("sigma_eff_pix")
        rms = float(cut.get("rms_nMgy", 0.))
        if (identity is None or sigma is None or
                not np.isfinite(peak) or peak <= 0 or
                not np.isfinite(float(sigma)) or float(sigma) <= 0):
            continue
        group = ("gaia_dr3" if identity.get("gaia_source_id") is not None
                 else "tractor_psf_without_gaia")
        groups[group].append({
            "peak": peak, "width": float(sigma),
            "peak_to_local_rms": peak/rms if np.isfinite(rms) and rms > 0
                                 else None,
        })

    def median_value(rows, key):
        values = [item[key] for item in rows if item[key] is not None]
        return float(np.median(values)) if values else None

    result = {
        "schema": "desidera.psf0-identity-population-diagnostic/1",
        "is_selection_gate": False,
        "measurements": "NATIVE_POSITIVE_CORE_MOMENTS_NOT_PSF_FWHM",
        "populations": {
            name: {
                "n": len(rows),
                "median_peak_nMgy_per_pix": median_value(rows, "peak"),
                "median_positive_core_sigma_pix": median_value(rows, "width"),
                "median_native_peak_over_local_rms": median_value(
                    rows, "peak_to_local_rms"),
            } for name, rows in groups.items()
        },
        "peak_strata": [],
        "notes": [
            "Tractor TYPE=PSF does not independently certify a star.",
            "Positive-only core moments broaden at low S/N; a marginal "
            "width comparison across mismatched brightness distributions "
            "cannot demonstrate source contamination.",
            "Each fixed peak-flux stratum is descriptive, not a pass/fail "
            "cut; low counts provide insufficient brightness-matched evidence.",
            "Fitting, holdout reservation, all rejection thresholds and "
            "science adoption are unaffected by this diagnostic.",
        ],
    }
    edges = (0., .1, .3, 1., 3., 10., float("inf"))
    for lo, hi in zip(edges[:-1], edges[1:]):
        gaia = [q for q in groups["gaia_dr3"]
                if lo <= q["peak"] < hi]
        tractor = [q for q in groups["tractor_psf_without_gaia"]
                   if lo <= q["peak"] < hi]
        result["peak_strata"].append({
            "peak_lower_inclusive_nMgy_per_pix": float(lo),
            "peak_upper_exclusive_nMgy_per_pix": (
                float(hi) if np.isfinite(hi) else None),
            "gaia_n": len(gaia),
            "tractor_only_n": len(tractor),
            "gaia_median_positive_core_sigma_pix": median_value(
                gaia, "width"),
            "tractor_only_median_positive_core_sigma_pix": median_value(
                tractor, "width"),
            "brightness_matched_comparison_supported": (
                len(gaia) >= 3 and len(tractor) >= 3),
        })
    gaia_width = result["populations"]["gaia_dr3"][
        "median_positive_core_sigma_pix"]
    tractor_width = result["populations"]["tractor_psf_without_gaia"][
        "median_positive_core_sigma_pix"]
    result["unmatched_median_width_ratio_tractor_to_gaia"] = (
        tractor_width/gaia_width
        if gaia_width is not None and tractor_width is not None else None)
    result["n_strata_with_at_least_3_per_population"] = sum(
        e["brightness_matched_comparison_supported"]
        for e in result["peak_strata"])
    return result


def build_empirical_coadd_psf(
    frame: NativeBandFrame, *,
    target_icrs_deg: tuple[float, float],
    target_protection_arcsec: float,
    config: PSF0Config | None = None,
    catalog_matcher=None,
) -> PSF0Result:
    """Build an exploratory native-grid ePSF from independent local star cutouts.

    No automatic archive access, source substitution, external Gaia query,
    wide-wing extrapolation, or scientific adoption. Fails when the stellar
    sample is too small or insufficiently isolated.
    """
    if not isinstance(frame, NativeBandFrame):
        raise TypeError("Pass NativeBandFrame, never a remote/preview image")
    cfg = config if config is not None else PSF0Config()
    if not isinstance(cfg, PSF0Config):
        raise TypeError("Expected PSF0Config")
    if cfg.star_catalog_policy in ("gaia_strict", "tractor_psf_validated"):
        from lsbpipe.photometry.psf_tractor_gaia import TractorGaiaMatcher
        if not isinstance(catalog_matcher, TractorGaiaMatcher):
            code = ("GAIA_STRICT_REQUIRES_NATIVE_DR11_TRACTOR"
                    if cfg.star_catalog_policy == "gaia_strict" else
                    "TRACTOR_PSF_VETTED_REQUIRES_NATIVE_DR11_TRACTOR")
            raise ValueError(
                code + ": download/verify the same-brick Tractor "
                "catalogue in GUI")
        if catalog_matcher.dataset_id != frame.dataset_id:
            raise ValueError("Gaia catalogue does not match exact native FITS band")
    elif catalog_matcher is not None:
        raise ValueError("DAO diagnostic cannot silently consume Gaia catalogue")
    try:
        import photutils
        import astropy
        from astropy.stats import sigma_clipped_stats
        from photutils.detection import DAOStarFinder
        from photutils.psf import EPSFBuilder, EPSFStar, EPSFStars
    except ImportError as exc:
        raise RuntimeError("Empirical PSF requires photutils and astropy; install -e '.[exact]'") from exc
    data = np.asarray(frame.image_nMgy)
    protected = _target_exclusion(frame, target_icrs_deg, target_protection_arcsec)
    valid = np.array(frame.valid, dtype=bool, copy=True)
    for key in (f"SATUR_{frame.band.upper()}", f"ALLMASK_{frame.band.upper()}"):
        if key in frame.mask_reasons:
            valid &= ~frame.mask_reasons[key]
    sample = valid & ~protected
    if sample.sum() < 400:
        raise ValueError("Insufficient background pixels outside protected Leo P/target region")
    _, sky0, std0 = sigma_clipped_stats(data[sample], sigma=3., maxiters=6)
    if not np.isfinite(std0) or std0 <= 0:
        raise ValueError("Cannot estimate an initial detection noise scale")
    detection_image = np.where(sample, data-sky0, 0.)
    finder = DAOStarFinder(
        threshold=cfg.detection_sigma*std0,
        fwhm=cfg.guessed_fwhm_pix,
        exclude_border=True)
    detections = finder(detection_image)
    if detections is None or len(detections) == 0:
        raise ValueError("No stellar candidates detected; PSF-0 cannot be built")
    # Split source identification and star-quality selection. All positive
    # detections are used for isolation even if a candidate is later rejected.
    # Photutils 2.x and 3.x use different finder centroid column names.
    # Resolve the documented schema explicitly instead of silently losing
    # candidates or assuming the installed release.
    names = set(detections.colnames)
    xkey = "x_centroid" if "x_centroid" in names else "xcentroid"
    ykey = "y_centroid" if "y_centroid" in names else "ycentroid"
    if not {xkey, ykey, "peak"}.issubset(names):
        raise ValueError("Unsupported photutils DAOStarFinder column schema")
    candidates = sorted(
        [(float(row[xkey]), float(row[ykey]), float(row["peak"]))
         for row in detections
         if np.isfinite(row[xkey]) and np.isfinite(row[ykey])
         and np.isfinite(row["peak"])],
        key=lambda item: (-item[2], item[1], item[0]))
    if len(candidates) < cfg.min_train_stars+cfg.min_holdout_stars:
        raise ValueError("Too few detected star candidates for an independent PSF holdout")
    positions = np.array([item[:2] for item in candidates])
    neighbors = cKDTree(positions)
    # Diagnose catalogue-confirmed potential stars missed by DAO, whose
    # >=3.0 default min_separation is 2.5*FWHM.  This is strictly read-only:
    # no new candidate enters the PSF reconstruction from the audit.
    gaia_seed_discovery_qa = (
        audit_gaia_catalog_seeds_vs_dao(
            catalog_matcher, positions, frame_shape=frame.shape,
            protected=protected, band=frame.band,
            cutout_size_pix=cfg.cutout_size_pix,
            guessed_fwhm_pix=cfg.guessed_fwhm_pix)
        if catalog_matcher is not None else None)
    parameter_provenance_qa = audit_psf0_parameters(cfg)
    rejected = {}
    screening_audit = []
    screening_by_id = {}
    def reject(reason, evidence=None):
        rejected[reason] = rejected.get(reason, 0)+1
        if evidence is not None:
            evidence["outcome"] = "REJECTED"
            evidence["first_failure"] = reason
    accepted = []
    cutouts = {}
    used_gaia_ids = set()
    screened_count = 0
    max_candidates = (
        len(candidates)
        if cfg.star_catalog_policy in ("gaia_strict", "tractor_psf_validated")
        else max(cfg.max_training_stars*5, 100))
    # Sort by brightness for capping, then spatially sort/split below.
    for ix, (x, y, peak) in enumerate(candidates[:max_candidates]):
        screened_count += 1
        star_id = f"star-{ix+1:04d}"
        near_dist = neighbors.query([x,y], k=2)[0]
        evidence = {
            "star_id": star_id,
            "native_xy_pix": [float(x), float(y)],
            "dao_peak_nMgy_per_pix": float(peak),
            "nearest_dao_detection_neighbor_pix": float(near_dist[1]),
            "dao_isolation_radius_pix": float(cfg.min_neighbor_separation_pix),
            "checks_passed": ["dao_detection"],
            "first_failure": None, "outcome": "IN_PROGRESS",
        }
        screening_audit.append(evidence)
        screening_by_id[star_id] = evidence
        identity = None
        if catalog_matcher is not None:
            identity, failure, catalog_evidence = catalog_matcher.match(
                x, y, band=frame.band,
                neighbor_search_radius_pix=min(
                    50., max(3*cfg.guessed_fwhm_pix,
                             .65*cfg.cutout_size_pix)),
                require_gaia=(cfg.star_catalog_policy == "gaia_strict"),
                return_audit=True)
            evidence["tractor_screen"] = catalog_evidence
            if failure is not None:
                reject(failure, evidence)
                continue
            evidence["checks_passed"].append("tractor_catalog")
        if len(neighbors.query_ball_point(
                [x,y], r=cfg.min_neighbor_separation_pix)) != 1:
            reject("NEIGHBOR_WITHIN_ISOLATION_RADIUS", evidence)
            continue
        evidence["checks_passed"].append("dao_isolation")
        data_cut, cause = _star_cutout(frame, x, y, cfg, protected)
        if cause:
            reject(cause, evidence)
            continue
        evidence["checks_passed"].append("native_cutout_and_local_sky")
        evidence["sky_qa"] = dict(data_cut.get("sky_qa", {}))
        evidence["local_sky_nMgy_per_pix"] = float(data_cut["sky_nMgy"])
        evidence["local_sky_rms_nMgy_per_pix"] = float(data_cut["rms_nMgy"])
        if identity is not None:
            shape = assess_psf_star_cutout(
                data_cut, fwhm_guess_pix=cfg.guessed_fwhm_pix)
            evidence["image_morphology"] = shape
            if not shape["accepted"]:
                reject(shape["reason"], evidence)
                continue
            evidence["checks_passed"].append("image_core_shape")
            gaia_id = identity["gaia_source_id"]
            if gaia_id is not None and gaia_id in used_gaia_ids:
                reject("GAIA_CATALOG_SOURCE_REUSED", evidence)
                continue
            if gaia_id is not None:
                used_gaia_ids.add(gaia_id)
            data_cut["star_identity"] = identity
            data_cut["shape_qa"] = shape
        ra, dec = frame.wcs.all_pix2world(x, y, 0)
        if not np.isfinite(ra) or not np.isfinite(dec):
            reject("INVALID_STAR_WCS", evidence)
            continue
        evidence["checks_passed"].append("valid_icrs_position")
        evidence["icrs_deg"] = [float(ra)%360, float(dec)]
        evidence["outcome"] = "ACCEPTED_BEFORE_WIDTH_SCREEN"
        accepted.append((star_id, x, y, float(ra)%360, float(dec),
                         peak, data_cut))
        cutouts[star_id] = data_cut
        # In catalog-vetted modes screen the entire DAO inventory so
        # the Gaia validation pool is not silently truncated by the
        # brighter Tractor-only training stars. The training cap is
        # applied AFTER a fixed, independent Gaia holdout has been set.
        if (cfg.star_catalog_policy == "dao_diagnostic"
                and len(accepted) >= cfg.max_training_stars + max(
                    cfg.min_holdout_stars,
                    cfg.max_training_stars//(cfg.holdout_every-1))):
            break
    accepted_before_width_screen = len(accepted)
    width_qa = None
    if catalog_matcher is not None:
        # Define the field width reference ONLY from Gaia-confirmed cores,
        # including in the Tractor-training experiment. Otherwise a
        # mixture of unverified non-Gaia objects would silently redefine
        # the star-size gate used to validate its own membership.
        reference_entries = [
            entry for entry in accepted
            if (entry[-1].get("star_identity") or {}).get(
                "gaia_source_id") is not None]
        width_qa = assess_sample_widths([
            (entry[0], entry[-1]["shape_qa"])
            for entry in reference_entries])
        if width_qa is not None:
            width_qa["reference_population"] = "GAIA_DR3_CORES_ONLY"
            width_qa["n_reference_gaia_candidates"] = len(reference_entries)

            compact = []
            for entry in accepted:
                sigma = entry[-1]["shape_qa"]["sigma_eff_pix"]
                ratio = sigma / width_qa["reference_core_sigma_pix"]
                if not (width_qa["min_relative_core_sigma"] <= ratio <=
                        width_qa["max_relative_core_sigma"]):
                    reject("GAIA_STELLAR_CORE_SIZE_OUTLIER",
                           screening_by_id[entry[0]])
                    continue
                compact.append(entry)
            accepted = compact
    for entry in accepted:
        evidence = screening_by_id[entry[0]]
        evidence["outcome"] = "ACCEPTED_FOR_SPLIT"
        evidence["checks_passed"].append("field_relative_core_width")
    # Prevent the brightest section of one brick monopolizing the holdout.
    accepted.sort(key=lambda t: (int(t[2]//(cfg.cutout_size_pix*2)),
                                 int(t[1]//(cfg.cutout_size_pix*2)), t[1], t[0]))
    minimum_stars = cfg.min_train_stars + cfg.min_holdout_stars
    n_gaia_eligible = sum(
        (entry[-1].get("star_identity") or {}).get("gaia_source_id")
        is not None for entry in accepted)
    accepted_identity_population_qa = (
        _identity_population_qa(accepted) if catalog_matcher else None)

    def selection_rejection(reason, stage):
        # A scientifically insufficient training/validation inventory must
        # never turn into an ordinary task error or an adopted model.
        accepted_candidates = []
        for entry in accepted:
            screening_by_id[entry[0]]["selection_role"] = (
                "ACCEPTED_BUT_SAMPLE_REJECTED_BEFORE_SPLIT")
        for star_id, x, y, ra, dec, peak, cut in accepted:
            identity = cut.get("star_identity") or {}
            accepted_candidates.append({
                "star_id": star_id, "x_pix": float(x), "y_pix": float(y),
                "ra_deg": float(ra), "dec_deg": float(dec),
                "peak_nMgy": float(peak),
                "cutout_sky_nMgy": float(cut["sky_nMgy"]),
                "gradient_sigma_span": float(cut["gradient_sigma_span"]),
                "gaia_source_id": identity.get("gaia_source_id"),
                "tractor_objid": identity.get("tractor_objid"),
                "tractor_ref_cat": identity.get("tractor_ref_cat"),
            })
        raise PSF0QualityError(reason, {
            "stage": stage,
            "n_detected_candidates": len(candidates),
            "n_screened_candidates": screened_count,
            "n_unexamined_candidates": len(candidates)-screened_count,
            "n_accepted_before_width_screen": accepted_before_width_screen,
            "n_accepted_stars": len(accepted),
            "n_gaia_holdout_eligible": n_gaia_eligible,
            "minimum_accepted_stars": minimum_stars,
            "min_train_stars": cfg.min_train_stars,
            "min_holdout_stars": cfg.min_holdout_stars,
            "rejected_reasons": dict(rejected),
            "accepted_candidates": accepted_candidates,
            "candidate_screening_audit": screening_audit,
            "parameter_provenance_qa": parameter_provenance_qa,
            "gaia_catalog_seed_vs_dao_qa": gaia_seed_discovery_qa,
            "catalog_selection_qa": {
                "policy": cfg.star_catalog_policy,
                "catalog_sha256": (
                    catalog_matcher.source_sha256
                    if catalog_matcher is not None else None),
                "width_screen": width_qa,
                "accepted_identity_population_qa":
                    accepted_identity_population_qa,
                "holdout_requires_gaia_dr3": (
                    cfg.star_catalog_policy == "tractor_psf_validated"),
            },
            "psf_recipe": asdict(cfg),
            "notes": [
                "Selection ended before ePSF fitting and before independent "
                "heldout validation.",
                "Tractor-only candidates are not Gaia-confirmed. No source "
                "is promoted to Gaia identity by this policy.",
                "Keep the same masked-pixel, isolation and source-shape "
                "rules; validate proposed changes on heldout Gaia stars.",
                "Native FITS and exact DR11 Tractor catalogue are read-only.",
            ],
        })
    if len(accepted) < minimum_stars:
        selection_rejection(
            f"Insufficient isolated, unsaturated, sky-screened stars: "
            f"{len(accepted)} (minimum {minimum_stars})",
            "stellar_sample_underflow")
    if (cfg.star_catalog_policy == "tractor_psf_validated"
            and width_qa is None):
        selection_rejection(
            "Cannot calibrate the Tractor-only core-width screen: "
            "fewer than 5 individually accepted Gaia DR3 reference cores. "
            "Do not use non-Gaia candidates to define their own width prior.",
            "gaia_width_reference_underflow")
    # Gaia-only validation is reserved BEFORE fitting and BEFORE any
    # train-only iterative outlier removal. This heldout set cannot include
    # a Tractor-only training source, preventing identity leakage.
    if cfg.star_catalog_policy == "tractor_psf_validated":
        gaia_indices = [
            i for i, entry in enumerate(accepted)
            if (entry[-1].get("star_identity") or {}).get(
                "gaia_source_id") is not None]
        if len(gaia_indices) < cfg.min_holdout_stars:
            selection_rejection(
                f"Insufficient independent Gaia DR3 holdout stars: "
                f"{len(gaia_indices)} (minimum {cfg.min_holdout_stars}); "
                "Tractor-only sources cannot substitute for Gaia validation",
                "gaia_holdout_underflow")
        holdout_idx = set(gaia_indices[::cfg.holdout_every])
        if len(holdout_idx) < cfg.min_holdout_stars:
            holdout_idx.update(gaia_indices[:cfg.min_holdout_stars])
    else:
        holdout_idx = {
            i for i in range(len(accepted)) if i % cfg.holdout_every == 0}
        if len(holdout_idx) < cfg.min_holdout_stars:
            holdout_idx.update(range(cfg.min_holdout_stars))
    for i, entry in enumerate(accepted):
        screening_by_id[entry[0]]["selection_role"] = (
            "HOLDOUT_GAIA_DR3" if i in holdout_idx
            and cfg.star_catalog_policy == "tractor_psf_validated"
            else "HOLDOUT" if i in holdout_idx else "TRAIN_CANDIDATE")
    training_entries = [v for i,v in enumerate(accepted) if i not in holdout_idx]
    holdout_entries = [v for i,v in enumerate(accepted) if i in holdout_idx]
    if len(training_entries) < cfg.min_train_stars:
        raise ValueError("Independent holdout would leave too few training stars")
    unused_training_entries = training_entries[cfg.max_training_stars:]
    training_entries = training_entries[:cfg.max_training_stars]
    for entry in unused_training_entries:
        screening_by_id[entry[0]]["final_selection_role"] = (
            "UNUSED_TRAINING_CAP")
    for entry in training_entries:
        screening_by_id[entry[0]]["final_selection_role"] = "TRAIN_SELECTED"
    for entry in holdout_entries:
        # An independent holdout from DAO detections has no verified
        # stellar identity: it must NEVER be called a Gaia holdout.
        screening_by_id[entry[0]]["final_selection_role"] = (
            "DAO_UNVERIFIED_HOLDOUT"
            if cfg.star_catalog_policy == "dao_diagnostic"
            else "GAIA_HOLDOUT")
    # Split before reviewer exclusions. The holdout IDs/coordinates remain
    # identical in the next run, even if several train stars are excluded.
    original_training_entries = list(training_entries)
    train_ids = {entry[0] for entry in original_training_entries}
    excluded_ids = set(cfg.excluded_training_star_ids)
    invalid_exclusions = excluded_ids - train_ids
    if invalid_exclusions:
        raise ValueError(
            "Manual exclusions are not in this configuration's training "
            f"sample (or refer to protected holdouts): {sorted(invalid_exclusions)}")
    training_entries = [
        entry for entry in original_training_entries
        if entry[0] not in excluded_ids]
    for entry in original_training_entries:
        if entry[0] in excluded_ids:
            screening_by_id[entry[0]]["final_selection_role"] = (
                "MANUALLY_EXCLUDED_TRAIN")
    if len(training_entries) < cfg.min_train_stars:
        raise ValueError(
            f"Manual exclusion would leave only {len(training_entries)} "
            f"training stars; minimum is {cfg.min_train_stars}")
    def metadata(entry, role):
        ident, x, y, ra, dec, peak, c = entry
        identity = c.get("star_identity") or {}
        shape = c.get("shape_qa") or {}
        return PSF0Star(
            ident, x, y, ra, dec, c["peak_nMgy"],
            c["sky_nMgy"], c["rms_nMgy"],
            c["gradient_sigma_span"], role,
            gaia_source_id=identity.get("gaia_source_id"),
            gaia_g_mag=identity.get("gaia_g_mag"),
            image_axis_ratio=shape.get("axis_ratio"),
            image_core_sigma_pix=shape.get("sigma_eff_pix"))
    # Start with a fixed heldout inventory. The strict iterative screen
    # NEVER reads holdout pixels to determine training exclusions.
    holdout = tuple(metadata(v, "holdout") for v in holdout_entries)
    auto_excluded_ids = set()
    outlier_history = []
    def training_epsf_stars(entries):
        return EPSFStars([
            EPSFStar(
                entry[-1]["image"], weights=entry[-1]["weights"],
                cutout_center=entry[-1]["center_in_cutout"],
                id_label=entry[0])
            for entry in entries])
    # Retain explicit installed-version/solver policy information: photutils
    # 3.1 changed default smoothing and centroid-fit box behavior. Merely
    # hashing the Desidera recipe is not enough to reproduce this model.
    # photutils <3.1 has no wing_smoothing setting. Preserve the library
    # baseline on old versions; fail clearly rather than ignoring an
    # explicit A/B request for disabled wide-wing smoothing.
    import inspect
    kwargs = {
        "oversampling": 1,
        "shape": (cfg.cutout_size_pix, cfg.cutout_size_pix),
        "maxiters": cfg.max_iterations,
        "progress_bar": False,
    }
    if "wing_smoothing" in inspect.signature(EPSFBuilder).parameters:
        kwargs["wing_smoothing"] = cfg.wing_smoothing
    elif not cfg.wing_smoothing:
        raise RuntimeError(
            "The wing-smoothing A/B test requires photutils >=3.1 "
            "with EPSFBuilder wing_smoothing support")
    def build_for_training(entries):
        # EPSFBuilder holds per-call fitter state; create fresh on each
        # rebuild, never reuse its previous fitted-star state.
        b = EPSFBuilder(**kwargs)
        return b, b(training_epsf_stars(entries))
    builder, fitted = build_for_training(training_entries)
    for iteration in range(cfg.training_outlier_passes):
        current_psf = fitted.epsf if hasattr(fitted, "epsf") else fitted[0]
        raw_trial = np.asarray(current_psf.data, dtype=float)
        scores = []
        for entry in training_entries:
            try:
                scores.append(training_star_residual(
                    entry, raw_trial,
                    guessed_fwhm_pix=cfg.guessed_fwhm_pix))
            except ValueError as exc:
                # Fail clearly. A badly modelled star must not silently
                # receive a zero/benign score and remain in training.
                scores.append({"star_id":entry[0], "score":1e6,
                               "error":str(exc),
                               "metric":"UNSCORABLE_TRAIN_STAR"})
        rejected_ids, summary = choose_training_outliers(
            scores, min_train=cfg.min_train_stars)
        brightness = brightness_bias_audit(scores, rejected_ids)
        # Do not silently remove the brightest ePSF stars when their
        # shape-residual score is effectively a brightness rank. This
        # was observed on *real* Leo P (rho~0.89), where six such
        # exclusions degraded negative-wing and heldout QA.
        # The potential exclusions remain available in the report for
        # manual review, but are not applied to the science inventory.
        candidate_ids = rejected_ids
        if brightness["auto_trim_blocked"]:
            rejected_ids = ()
        summary["brightness_score_coupling_qa"] = brightness
        summary["proposed_before_brightness_guard"] = list(candidate_ids)
        # Cumulative cap to protect real spatial PSF variation from a
        # feedback loop that progressively deletes hard-to-fit regions.
        cap = max(1, int(.20 * len(original_training_entries)))
        budget = max(0, cap-len(auto_excluded_ids))
        rejected_ids = tuple(rejected_ids[:budget])
        summary.update({
            "iteration":iteration+1,
            "screened_stars":scores,
            "n_proposed_before_brightness_guard":len(candidate_ids),
            "n_removed_this_pass":len(rejected_ids),
            "selected_for_exclusion":list(rejected_ids),
            "cumulative_exclusion_budget":cap,
            "uses_holdout_to_select":False,
            "coadd_chi_square_calibrated":False,
        })
        outlier_history.append(summary)
        if not rejected_ids:
            break
        auto_excluded_ids.update(rejected_ids)
        for _ in rejected_ids:
            reject("TRAIN_PSF_RESIDUAL_OUTLIER_AUTO_EXPERIMENTAL")
        training_entries = [
            entry for entry in training_entries
            if entry[0] not in auto_excluded_ids]
        builder, fitted = build_for_training(training_entries)
    train = tuple(metadata(v, "train") for v in training_entries)
    score_amplitude_guard_triggered = any(
        item.get("brightness_score_coupling_qa", {}).get(
            "auto_trim_blocked", False)
        for item in outlier_history)
    solver_diagnostics = {
        "photutils_version": str(photutils.__version__),
        "smoothing_kernel_requested_or_default": str(
            getattr(builder, "smoothing_kernel", "LIBRARY_DEFAULT_NOT_REPORTED")),
        "smoothing_kernel_final_if_reported": str(
            getattr(fitted, "smoothing_kernel", "NOT_REPORTED")),
        "fit_shape_requested_or_default": str(
            getattr(builder, "fit_shape", "LIBRARY_DEFAULT_NOT_REPORTED")),
        "fit_shape_final_if_reported": str(
            getattr(fitted, "fit_shape", "NOT_REPORTED")),
        "wing_smoothing_requested": cfg.wing_smoothing,
        "wing_smoothing_explicitly_set": "wing_smoothing" in kwargs,
        "max_iterations_requested": cfg.max_iterations,
        "iterations_performed_if_reported": (
            int(fitted.iterations)
            if getattr(fitted, "iterations", None) is not None else None),
        "converged_if_reported": (
            bool(fitted.converged) if hasattr(fitted, "converged") else None),
        "final_converged_fraction_if_reported": (
            float(fitted.final_converged_fraction)
            if (getattr(fitted, "final_converged_fraction", None) is not None
                and math.isfinite(float(fitted.final_converged_fraction)))
            else None),
    }
    solver_diagnostics["convergence_fraction_target_if_reported"] = (
        float(getattr(builder, "converged_fraction"))
        if getattr(builder, "converged_fraction", None) is not None
        else None)
    # Astropy photutils >=3.1 exposes the per-iteration fit diagnostics.
    # Preserve the complete finite, JSON-safe convergence series, not just
    # a Boolean "converged=False"; this distinguishes a near pass from a
    # genuinely unstable optimizer. Do not change any science gating.
    def safe_float(value):
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return number if math.isfinite(number) else None

    iteration_records = []
    iteration_info = getattr(fitted, "iteration_info", None)
    if iteration_info is not None:
        fields = set(getattr(iteration_info, "colnames", []))
        for row in iteration_info:
            item = {}
            for key in ("iteration", "converged_fraction",
                        "max_center_shift", "common_center_shift",
                        "n_fit_failed", "max_epsf_change"):
                if key in fields:
                    item[key] = safe_float(row[key])
            if "stage" in fields:
                item["stage"] = str(row["stage"])
            if "converged" in fields:
                item["converged"] = bool(row["converged"])
            iteration_records.append(item)
    solver_diagnostics["iteration_history_if_reported"] = iteration_records
    versions = {
        "numpy": str(np.__version__),
        "scipy": str(__import__("scipy").__version__),
        "photutils": str(photutils.__version__),
        "astropy": str(astropy.__version__),
    }
    recenter = getattr(builder, "recentering_func", None)
    recenter_name = (
        f"{getattr(recenter, '__module__', 'unknown')}."
        f"{getattr(recenter, '__qualname__', 'library_default')}"
        if recenter is not None else "NOT_EXPOSED_LIBRARY_DEFAULT")
    method_fingerprint = _implementation_method_fingerprint(
        cfg, kwargs, versions, solver_diagnostics, recenter_name)
    solver_diagnostics["implementation_method_sha256"] = (
        method_fingerprint["sha256"])
    psf_model = fitted.epsf if hasattr(fitted, "epsf") else fitted[0]
    raw_kernel = np.asarray(psf_model.data, dtype=float)
    if raw_kernel.shape != (cfg.cutout_size_pix, cfg.cutout_size_pix):
        raise ValueError("Unexpected ePSF output grid/shape; refuse silent resampling")
    if not np.isfinite(raw_kernel).all():
        raise ValueError("Nonfinite ePSF solution")
    signed_diagnostics = signed_psf_qa(raw_kernel, cfg.guessed_fwhm_pix)
    # Prepared only in worker memory: never serialize observation cutouts
    # or present them as a certified science image or saved FITS product.
    review_stars = []
    from scipy.ndimage import shift as _shift_review_model
    for entry in original_training_entries:
        c = entry[-1]
        local_x, local_y = c["center_in_cutout"]
        center = (np.asarray(raw_kernel.shape)-1)/2.
        template = _shift_review_model(
            raw_kernel, (local_y-center[0], local_x-center[1]),
            order=3, mode="constant", cval=0.)
        yy, xx = np.indices(template.shape)
        near = (np.hypot(xx-local_x, yy-local_y) <= cfg.guessed_fwhm_pix)
        good = near & (c["weights"] > 0)
        weighted = c["weights"][good]**2
        denominator = float(np.sum(weighted*template[good]**2))
        amplitude = (float(np.sum(weighted*template[good]*c["image"][good])
                           / denominator) if denominator > 0 else 0.)
        # Model preview is signed and on the EXACT cutout's native grid.
        # It cannot be adopted or exported as a physical PSF from review.
        model = template * max(amplitude, 0.)
        review_stars.append({
            "star_id": entry[0],
            "role": ("excluded" if entry[0] in excluded_ids else
                     "auto_rejected" if entry[0] in auto_excluded_ids else
                     "train"),
            "auto_rejected_by_training_residual": (
                entry[0] in auto_excluded_ids),
            "gaia": (c.get("star_identity") or {}),
            "shape_qa": (c.get("shape_qa") or {}),
            "x_pix": float(entry[1]), "y_pix": float(entry[2]),
            "ra_deg": float(entry[3]), "dec_deg": float(entry[4]),
            "cutout": c["image"].copy(),
            "model": model,
            "valid": c["weights"] > 0,
            "center_in_cutout": (float(local_x), float(local_y)),
            "peak_nMgy": float(c["peak_nMgy"]),
            "sky_nMgy": float(c["sky_nMgy"]),
            "gradient_sigma_span": float(c["gradient_sigma_span"]),
            "sky_qa": dict(c.get("sky_qa", {})),
        })
    review_snapshot = {
        "schema": "desidera.psf0-star-review-in-memory/1",
        "status": "RESEARCHER_REVIEW_NOT_SCIENCE_ADOPTED",
        "guessed_fwhm_pix": cfg.guessed_fwhm_pix,
        "min_train_stars": cfg.min_train_stars,
        "catalog_policy": cfg.star_catalog_policy,
        "catalog_sha256": (
            catalog_matcher.source_sha256 if catalog_matcher else None),
        "excluded_training_star_ids": tuple(cfg.excluded_training_star_ids),
        "automatic_training_rejections": tuple(sorted(auto_excluded_ids)),
        "training_residual_screening_history": outlier_history,
        "sky_diagnostics": {
            "current_recipe_inner_annulus_in_guessed_fwhm":
                float(cfg.sky_annulus_inner_pix/cfg.guessed_fwhm_pix),
            "current_recipe_outer_annulus_in_guessed_fwhm":
                float(cfg.sky_annulus_outer_pix/cfg.guessed_fwhm_pix),
            "sky_wing_absorption_risk_unresolved":True,
            "sky_plus_psf_model_jointly_fitted":False,
            "sky_error_covariance_calibrated":False,
        },
        "stars": review_stars,
    }
    negative_fraction = signed_diagnostics["negative_absolute_mass_fraction"]
    final_train_ids = {e[0] for e in training_entries}
    holdout_ids = {e[0] for e in holdout_entries}
    sky_sample_qa = {
        "per_star": [{
            "star_id":entry[0],
            "role":("train" if entry[0] in final_train_ids else
                    "holdout" if entry[0] in holdout_ids else
                    "auto_rejected" if entry[0] in auto_excluded_ids else
                    "excluded"),
            **entry[-1].get("sky_qa", {}),
        } for entry in original_training_entries+holdout_entries],
        "ring_starts_inside_four_assumed_fwhm": (
            cfg.sky_annulus_inner_pix < 4*cfg.guessed_fwhm_pix),
        "coadd_sky_and_psf_wings_jointly_fitted":False,
        "sky_correlation_uncertainty_calibrated":False,
    }
    selection_catalog_qa = {
        "policy": cfg.star_catalog_policy,
        "gaia_strict": cfg.star_catalog_policy == "gaia_strict",
        "tractor_psf_validated": cfg.star_catalog_policy == "tractor_psf_validated",
        "holdout_requires_gaia_dr3": cfg.star_catalog_policy == "tractor_psf_validated",
        "n_detected_candidates": len(candidates),
        "n_screened_candidates": screened_count,
        "n_unexamined_candidates": len(candidates)-screened_count,
        "candidate_screening_audit": screening_audit,
        "parameter_provenance_qa": parameter_provenance_qa,
        "gaia_catalog_seed_vs_dao_qa": gaia_seed_discovery_qa,
        "n_unused_training_candidates_after_cap": len(
            unused_training_entries),
        "accepted_identity_population_qa":
            accepted_identity_population_qa,
        "training_identity_population_qa": (
            _identity_population_qa(training_entries)
            if catalog_matcher else None),
        "catalog_source_sha256": (
            catalog_matcher.source_sha256 if catalog_matcher else None),
        "catalog_path": (
            catalog_matcher.source_path if catalog_matcher else None),
        "n_tractor_primary_sources": (
            len(catalog_matcher.rows) if catalog_matcher else None),
        "image_moment_width_reference": width_qa,
        "sky_annulus_to_guessed_fwhm": [
            float(cfg.sky_annulus_inner_pix/cfg.guessed_fwhm_pix),
            float(cfg.sky_annulus_outer_pix/cfg.guessed_fwhm_pix)],
        "sky_and_psf_wings_jointly_estimated":False,
        "training_residual_screening_history": outlier_history,
        "automatic_training_rejections": sorted(auto_excluded_ids),
        "brightness_score_coupling_blocked_auto_trim":
            score_amplitude_guard_triggered,
        "n_train_gaia_matched": sum(s.gaia_source_id is not None for s in train),
        "n_train_tractor_only": (
            sum(s.gaia_source_id is None for s in train)
            if cfg.star_catalog_policy == "tractor_psf_validated" else 0),
        "n_train_unverified_dao": (
            len(train) if cfg.star_catalog_policy == "dao_diagnostic" else 0),
        "n_holdout_gaia_matched": sum(s.gaia_source_id is not None for s in holdout),
        "n_holdout_tractor_only": (
            sum(s.gaia_source_id is None for s in holdout)
            if cfg.star_catalog_policy == "tractor_psf_validated" else 0),
        "n_holdout_unverified_dao": (
            len(holdout) if cfg.star_catalog_policy == "dao_diagnostic" else 0),
        "holdout_identity_validation": (
            "NONE_DAO_CANDIDATES_ONLY"
            if cfg.star_catalog_policy == "dao_diagnostic"
            else "GAIA_DR3_IDENTIFIED_IN_NATIVE_TRACTOR"),
        "holdout_independent_of_epsf_fit": True,
        "psf_star_cleanliness_certified": False,
    }
    # Even when the signed kernel fails the negative-mass gate, preserve
    # independent holdout residual evidence. Fit amplitudes only in cores;
    # *never* clip the signed rejected model to manufacture a pass.
    signed_holdouts = []
    holdout_failures = []
    for entry in holdout_entries:
        c = entry[-1]
        try:
            signed_holdouts.append(heldout_signed_residual(
                source_id=entry[0], image=c["image"],
                sqrt_ivar=c["weights"], signed_kernel=raw_kernel,
                local_center_xy=c["center_in_cutout"],
                guessed_fwhm_pix=cfg.guessed_fwhm_pix,
                measured_peak=c["peak_nMgy"],
                local_sky_rms_nMgy_pix=c["rms_nMgy"]))
        except ValueError as exc:
            holdout_failures.append({
                "star_id": entry[0], "reason": str(exc)})
    signed_holdout_summary = summarize_signed_holdouts(signed_holdouts)
    signed_growth_summary = summarize_core_normalized_growth(signed_holdouts)
    signed_holdout_coverage = audit_signed_holdout_coverage(
        [entry[0] for entry in holdout_entries],
        signed_holdouts, holdout_failures)
    if negative_fraction > .025:
        # Preserve evidence needed to troubleshoot Leo P without allowing
        # this rejected kernel into a FITS export or science result.
        diagnostic = {
            "status": "PSF0_QUALITY_REJECTED_NOT_ADOPTED",
            "reason_code": "STRONG_NEGATIVE_EPSF_RINGING",
            "negative_absolute_mass_fraction": negative_fraction,
            "blocking_threshold_fraction": .025,
            "psf_recipe": asdict(cfg),
            "catalog_selection_qa": selection_catalog_qa,
            "sky_sample_qa": sky_sample_qa,
            "training_residual_screening_history": outlier_history,
            "reviewed_excluded_training_ids": list(cfg.excluded_training_star_ids),
            "signed_kernel_qa": signed_diagnostics,
            "signed_holdout_summary": signed_holdout_summary,
            "signed_holdout_core_normalized_flux_growth_summary": signed_growth_summary,
            "signed_holdout_coverage_qa": signed_holdout_coverage,
            "signed_holdout_details": signed_holdouts,
            "signed_holdout_failures": holdout_failures,
            "solver_diagnostics": solver_diagnostics,
            # An unaltered rejected model can be visualized in the GUI, but
            # never normalized, used for photometry or exported as a PSF.
            "signed_kernel_values_for_qa_only": raw_kernel.tolist(),
            "n_train": len(train),
            "n_holdout": len(holdout),
            "n_detected_candidates": len(candidates),
            "training": [asdict(star) for star in train],
            "holdout": [asdict(star) for star in holdout],
            "rejected_reasons": dict(rejected),
            "notes": [
                "The rejected EPSF kernel is NOT normalized, clamped or exported.",
                "Inspect star identity, local cutout sky, centroid alignment, "
                "the empirical core and the photutils recipe.",
                "Negative absolute mass is a conservative screening heuristic, "
                "not a statistically calibrated false-positive rate.",
                "Compare signed negative mass by guessed-FWHM annulus; do not "
                "weaken the gate until the stellar cutouts are independently assessed.",
            ],
        }
        raise PSF0QualityError(
            f"Strong negative EPSF ringing: negative absolute mass "
            f"{negative_fraction:.2%} exceeds the experimental 2.50% "
            f"screening limit (training {len(train)}, holdout {len(holdout)}).",
            diagnostic, review_snapshot=review_snapshot)
    if not signed_holdout_coverage["all_fixed_holdouts_and_annuli_scored"]:
        # Incomplete independent checking is a scientific rejection, NOT
        # an opportunity to replace weak/failed Gaia-heldout stars.
        diagnostic = {
            "status": "PSF0_QUALITY_REJECTED_NOT_ADOPTED",
            "stage": "holdout_diagnostic_incomplete",
            "reason_code": "INCOMPLETE_FIXED_HOLDOUT_RESIDUAL_QA",
            "catalog_selection_qa": selection_catalog_qa,
            "psf_recipe": asdict(cfg),
            "signed_kernel_qa": signed_diagnostics,
            "signed_holdout_summary": signed_holdout_summary,
            "signed_holdout_core_normalized_flux_growth_summary": signed_growth_summary,
            "signed_holdout_coverage_qa": signed_holdout_coverage,
            "signed_holdout_details": signed_holdouts,
            "signed_holdout_failures": holdout_failures,
            "signed_kernel_values_for_qa_only": raw_kernel.tolist(),
            "solver_diagnostics": solver_diagnostics,
            "n_train": len(train),
            "n_holdout": len(holdout),
            "training": [asdict(star) for star in train],
            "holdout": [asdict(star) for star in holdout],
            "notes": [
                "At least one fixed independent holdout or one radial "
                "zone was not scored. No successful experimental kernel "
                "may be returned by silently shrinking the validation set.",
                "The heldout inventory is unchanged; no replacement stars "
                "were chosen using residual scores.",
                "This is a completeness/integrity gate, not a covariance-"
                "calibrated model-accuracy criterion.",
            ],
        }
        raise PSF0QualityError(
            "Incomplete independent heldout-star residual QA: "
            f"{signed_holdout_coverage['n_successfully_scored_stars']} / "
            f"{signed_holdout_coverage['n_expected_holdout_stars']} "
            "signed holdouts available; some stars or radial zones "
            "lack valid evidence. No PSF was adopted.", diagnostic,
            review_snapshot=review_snapshot)
    if getattr(fitted, "converged", None) is False:
        # EPSFBuilder documents that a nonconverged ePSF can retain
        # percent-level shape and centroid biases. Keep the actual signed
        # diagnostic and disjoint Gaia holdouts available for inspection,
        # but do not return a normalized successful experimental model.
        diagnostic = {
            "status": "PSF0_QUALITY_REJECTED_NOT_ADOPTED",
            "stage": "epsf_builder_not_converged",
            "reason_code": "EPSF_BUILDER_NOT_CONVERGED",
            "catalog_selection_qa": selection_catalog_qa,
            "psf_recipe": asdict(cfg),
            "signed_kernel_qa": signed_diagnostics,
            "signed_holdout_summary": signed_holdout_summary,
            "signed_holdout_core_normalized_flux_growth_summary": signed_growth_summary,
            "signed_holdout_coverage_qa": signed_holdout_coverage,
            "signed_holdout_details": signed_holdouts,
            "signed_holdout_failures": holdout_failures,
            "signed_kernel_values_for_qa_only": raw_kernel.tolist(),
            "solver_diagnostics": solver_diagnostics,
            "n_train": len(train), "n_holdout": len(holdout),
            "training": [asdict(star) for star in train],
            "holdout": [asdict(star) for star in holdout],
            "notes": [
                "Photutils explicitly reported nonconvergence. Its final "
                "converged-star fraction cannot substitute for the overall "
                "EPSFBuilder converged Boolean.",
                "The displayed signed kernel is DIAGNOSTIC ONLY. Increasing "
                "the iteration budget must be a controlled A/B experiment, "
                "not an automatic repair or science adoption.",
            ],
        }
        raise PSF0QualityError(
            "Photutils ePSFBuilder did not converge within "
            f"{cfg.max_iterations} iterations; no scientifically "
            "acceptable provisional PSF was produced.",
            diagnostic, review_snapshot=review_snapshot)
    kernel = np.maximum(raw_kernel, 0.)
    if kernel.sum() <= 0:
        raise ValueError("Nonpositive empirical PSF normalization")
    kernel /= kernel.sum()

    # Audit the delivered nonnegative, unit-flux kernel ON THE SAME fixed
    # holdout stars/cutouts/radii as the pre-clipping signed candidate.
    # These are separate diagnostics. A signed-kernel holdout score is
    # not evidence about the renormalized kernel used by downstream fits.
    final_holdouts = []
    final_holdout_failures = []
    for entry in holdout_entries:
        star_data = entry[-1]
        try:
            final_holdouts.append(heldout_final_kernel_residual(
                source_id=entry[0], image=star_data["image"],
                sqrt_ivar=star_data["weights"], final_kernel=kernel,
                local_center_xy=star_data["center_in_cutout"],
                guessed_fwhm_pix=cfg.guessed_fwhm_pix,
                measured_peak=star_data["peak_nMgy"],
                local_sky_rms_nMgy_pix=star_data["rms_nMgy"]))
        except ValueError as exc:
            final_holdout_failures.append({
                "star_id": entry[0], "reason": str(exc)})
    final_holdout_summary = summarize_signed_holdouts(final_holdouts)
    final_growth_summary = summarize_core_normalized_growth(final_holdouts)
    final_holdout_coverage = audit_signed_holdout_coverage(
        [entry[0] for entry in holdout_entries],
        final_holdouts, final_holdout_failures)
    final_holdout_summary["kernel_representation"] = (
        "FINAL_NONNEGATIVE_NORMALIZED")
    final_holdout_summary["not_covariance_calibrated"] = True
    final_holdout_coverage["evaluated_kernel"] = (
        "FINAL_NONNEGATIVE_NORMALIZED")
    if not final_holdout_coverage["all_fixed_holdouts_and_annuli_scored"]:
        diagnostic = {
            "status": "PSF0_QUALITY_REJECTED_NOT_ADOPTED",
            "reason_code": "INCOMPLETE_FINAL_KERNEL_HOLDOUT_QA",
            "psf_recipe": asdict(cfg),
            "catalog_selection_qa": selection_catalog_qa,
            "signed_kernel_qa": signed_diagnostics,
            "signed_holdout_summary": signed_holdout_summary,
            "signed_holdout_core_normalized_flux_growth_summary": signed_growth_summary,
            "signed_holdout_coverage_qa": signed_holdout_coverage,
            "final_holdout_summary_after_clipping": final_holdout_summary,
            "final_holdout_core_normalized_flux_growth_summary_after_clipping": final_growth_summary,
            "final_holdout_coverage_after_clipping": final_holdout_coverage,
            "final_holdout_details_after_clipping": final_holdouts,
            "final_holdout_failures_after_clipping": final_holdout_failures,
            "solver_diagnostics": solver_diagnostics,
            "n_train": len(train), "n_holdout": len(holdout),
            "training": [asdict(star) for star in train],
            "holdout": [asdict(star) for star in holdout],
            "notes": [
                "The final nonnegative kernel failed complete three-zone "
                "holdout QA; no usable experimental PSF was returned.",
                "No holdout star was replaced or reclassified.",
                "Signed pre-clipping QA remains separate and unchanged.",
            ],
        }
        raise PSF0QualityError(
            "Final nonnegative normalized PSF lacks complete independent "
            "holdout residuals in all three radial zones; model rejected.",
            diagnostic, review_snapshot=review_snapshot)

    checks = []
    for entry in holdout_entries:
        checks.append(_holdout_diagnostic(metadata(entry, "holdout"), entry[-1], kernel))
    med_core_resid = float(np.median([x["relative_core_abs_residual"] for x in checks]))
    flags = [
        "CORE_PSF_ONLY_EXTENDED_WINGS_UNVALIDATED",
        "SPATIAL_REPRESENTATIVITY_NOT_VALIDATED",
        "CORRELATED_COADD_NOISE_UNMODELED",
        ("SKY_ANNULUS_INSIDE_4X_GUESSED_FWHM_WING_RISK"
         if cfg.sky_annulus_inner_pix < 4*cfg.guessed_fwhm_pix
         else "STELLAR_WINGS_NOT_ESTIMATED_IN_SKY_FIT"),
        ("PSF_STAR_IDENTITIES_NOT_GAIA_VERIFIED"
         if catalog_matcher is None else
         "GAIA_IDENTITIES_VERIFIED_CUTOUT_CLEANLINESS_UNCALIBRATED"
         if cfg.star_catalog_policy == "gaia_strict" else
         "TRACTOR_ONLY_TRAINING_IDENTITY_NOT_GAIA_CONFIRMED"),
        "CUTOUT_SKY_MAY_ABSORB_WINGS",
        "FITTED_STAR_FLUX_NOT_TOTAL_FLUX",
        "TRAINING_RESIDUAL_SCORE_IN_SAMPLE_NOT_CROSSVALIDATED",
        "HOLDOUT_RESIDUAL_NOT_CALIBRATED_CHI_SQUARE",
        "TARGET_PROTECTION_LOCAL_WCS_APPROXIMATION",
    ]
    if cfg.star_catalog_policy == "tractor_psf_validated":
        flags.append("TRACTOR_ONLY_TRAINING_STARS_NOT_GAIA_CONFIRMED")
        flags.append("GAIA_ONLY_HOLDOUT_VALIDATION_NOT_CALIBRATED")
    if score_amplitude_guard_triggered:
        flags.append("AUTO_TRAINING_TRIM_BLOCKED_BY_SCORE_FLUX_COUPLING")
    if negative_fraction > 1e-4:
        flags.append("EPSF_NEGATIVE_RINGING_CLIPPED")
    if not hasattr(fitted, "converged"):
        # Older Photutils returns only an (epsf, fitted_stars) tuple.
        # This does NOT demonstrate convergence: preserve an explicit
        # uncertainty flag, never silently treat the missing Boolean as True.
        flags.append("EPSF_CONVERGENCE_NOT_REPORTED_BY_LIBRARY")
    source_hashes = _verified_source_hashes(frame)
    lineage = None
    if source_hashes is None:
        flags.append("SYNTHETIC_TEST_INPUT" if frame.source_evidence.get("synthetic") else
                     "SOURCE_HASHES_NOT_VERIFIED")
        source_hashes = {}
    else:
        selection_sources = dict(source_hashes)
        if catalog_matcher is not None:
            selection_sources["tractor_dr11_gaia"] = (
                catalog_matcher.source_sha256)
        digest = sha256(json.dumps(selection_sources, sort_keys=True,
                                   separators=(",", ":")).encode("utf8")).hexdigest()
        selection_id = sha256(json.dumps(
            [(s.star_id, s.x_pix, s.y_pix, s.role) for s in train+holdout],
            sort_keys=True).encode("utf8")).hexdigest()
        star_sky_id = sha256(json.dumps(
            [(s.star_id, s.cutout_sky_nMgy, s.cutout_rms_nMgy,
              s.gradient_sigma_span) for s in train+holdout],
            sort_keys=True).encode("utf8")).hexdigest()
        lineage = PSFLineageKey(
            observation_id=frame.dataset_id, instrument=(
                "BASS_90PRIME" if frame.region == "north" and frame.band in ("g","r")
                else "MZLS_MOSAIC3" if frame.region == "north" and frame.band == "z"
                else "DECAM"),
            band=frame.band, source_sha256=digest,
            star_selection_sha256=selection_id,
            cutout_sky_sha256=star_sky_id,
            recipe_sha256=method_fingerprint["sha256"],
            model_scope="global").digest
    instrument = ("BASS_90PRIME" if frame.region == "north" and frame.band in ("g","r")
                  else "MZLS_MOSAIC3" if frame.region == "north" and frame.band == "z"
                  else "DECAM")
    # Fixed-kernel joint native scene diagnostics: fit each SAME selected
    # star and its original Tractor PSF neighbors with an unconstrained
    # planar sky on a larger ORIGINAL coadd patch. These nuisance fits
    # NEVER alter the already constructed and heldout-tested ePSF.
    # This is not extended-wing recovery and is not noise-covariance QA.
    joint_native_scene_qa = audit_fixed_psf_joint_scenes(
        frame, kernel, train+holdout, catalog_matcher,
        protected_mask=protected,
        guessed_fwhm_pix=cfg.guessed_fwhm_pix)
    # FWHM here is only the guessed finder width, NOT measured EPSF FWHM.
    metrics = {
        "fixed_psf_joint_native_scene_qa": joint_native_scene_qa,
        "catalog_selection_qa": selection_catalog_qa,
        "sky_sample_qa": sky_sample_qa,
        "parameter_provenance_qa": parameter_provenance_qa,
        "implementation_method_sha256": method_fingerprint["sha256"],
        "implementation_method": method_fingerprint["payload"],
        "training_residual_screening_history": outlier_history,
        "holdout_median_abs_core_residual_over_peak": med_core_resid,
        "holdout_diagnostics": checks,
        "epsf_negative_mass_fraction_before_clipping": negative_fraction,
        "signed_epsf_diagnostics_before_clipping": signed_diagnostics,
        "signed_holdout_summary": signed_holdout_summary,
        "signed_holdout_core_normalized_flux_growth_summary": signed_growth_summary,
        "signed_holdout_coverage_qa": signed_holdout_coverage,
        "signed_holdout_details": signed_holdouts,
        "signed_holdout_failures": holdout_failures,
        "final_holdout_summary_after_clipping": final_holdout_summary,
        "final_holdout_core_normalized_flux_growth_summary_after_clipping": final_growth_summary,
        "final_holdout_coverage_after_clipping": final_holdout_coverage,
        "final_holdout_details_after_clipping": final_holdouts,
        "final_holdout_failures_after_clipping": final_holdout_failures,
        "solver_diagnostics": solver_diagnostics,
        "initial_detection_background_nMgy_per_pixel": float(sky0),
        "initial_detection_rms_nMgy_per_pixel": float(std0),
        "assumed_detection_fwhm_pix_not_measured": cfg.guessed_fwhm_pix,
        "epsf_converged_reported": (bool(fitted.converged)
                                    if hasattr(fitted, "converged") else None),
    }
    return PSF0Result(
        dataset_id=frame.dataset_id, instrument=instrument, kernel=kernel,
        training=train, holdout=holdout,
        rejected_reasons=rejected, detection_count=len(candidates),
        metrics=metrics, flags=tuple(flags), recipe=cfg,
        source_hashes=source_hashes, lineage_id=lineage,
        center_target_icrs=(float(target_icrs_deg[0]),float(target_icrs_deg[1])),
        target_protection_arcsec=float(target_protection_arcsec),
        review_snapshot=review_snapshot,
        algorithm_versions={
            "recipe": RECIPE, **versions,
            "implementation_method_sha256": method_fingerprint["sha256"],
        })
