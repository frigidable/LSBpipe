"""Explicit scientific stage dependencies for the PSF-first Desidera workflow.

Pure planning/readiness metadata. This is not a scheduler, a numerical PSF
builder, a sky fitter, a validation result, or an adopted photometric run.

The DAG deliberately excludes iterative back-edges: refinement creates a NEW
version of changed upstream artifacts; their descendants become stale.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import math
import re
from types import MappingProxyType
from typing import Iterable, Mapping

STAGE_ORDER = (
    "data_preparation",
    "scene_initialization",
    "preliminary_sky",
    "psf_star_selection",
    "psf_cutout_sky",
    "psf_reconstruction",
    "manual_mask_draft",
    "final_scene_masks",
    "sky_reconstruction",
    "refinement_assessment",
    "galaxy_photometry",
)

# Actual declared inputs at the artifact level, not UI tab order.
# The galaxy's final residual sky is NOT an input to stellar PSF production.
# A saved manual edit is NOT an input to stellar PSF unless it explicitly
# alters the dedicated star selection or PSF-cutout background artifacts.
DEPENDENCIES = MappingProxyType({
    "data_preparation": (),
    "scene_initialization": ("data_preparation",),
    "preliminary_sky": ("data_preparation", "scene_initialization"),
    "psf_star_selection": ("data_preparation", "scene_initialization", "preliminary_sky"),
    "psf_cutout_sky": ("data_preparation", "psf_star_selection", "preliminary_sky"),
    "psf_reconstruction": ("data_preparation", "psf_star_selection", "psf_cutout_sky"),
    "manual_mask_draft": (),
    "final_scene_masks": ("data_preparation", "scene_initialization",
                          "psf_reconstruction", "manual_mask_draft"),
    "sky_reconstruction": ("data_preparation", "preliminary_sky", "final_scene_masks"),
    "refinement_assessment": ("psf_reconstruction", "final_scene_masks",
                              "sky_reconstruction"),
    "galaxy_photometry": ("data_preparation", "psf_reconstruction",
                          "final_scene_masks", "sky_reconstruction",
                          "refinement_assessment"),
})
assert tuple(DEPENDENCIES) == STAGE_ORDER
assert all(STAGE_ORDER.index(parent) < STAGE_ORDER.index(child)
           for child, parents in DEPENDENCIES.items() for parent in parents)


def invalidated_descendants(changed: Iterable[str]) -> tuple[str, ...]:
    """List *downstream* products needing fresh receipts, in topological order.

    Does not schedule anything or modify saved products. A rejected/manual
    smart-mask draft is not considered a certified final scene artifact.
    """
    if isinstance(changed, str):
        raise TypeError("Supply an iterable of stage names, not one string")
    seeds = frozenset(changed)
    unknown = seeds.difference(DEPENDENCIES)
    if unknown:
        raise ValueError("Unknown artifact stages: " + ", ".join(sorted(map(str, unknown))))
    stale = set(seeds)
    for name in STAGE_ORDER:
        if any(parent in stale for parent in DEPENDENCIES[name]):
            stale.add(name)
    return tuple(name for name in STAGE_ORDER if name in stale and name not in seeds)


def require_stage_dependencies(stage: str, present: Iterable[str]) -> tuple[str, ...]:
    """Return missing declared immediate dependencies (no auto-adoption)."""
    if stage not in DEPENDENCIES:
        raise ValueError("Unknown stage")
    present_set = frozenset(present)
    return tuple(parent for parent in DEPENDENCIES[stage] if parent not in present_set)


_HEX_SHA = re.compile(r"^[0-9a-f]{64}$")
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]*$")


@dataclass(frozen=True)
class PSFLineageKey:
    """Cache identity of an optical observation-bound PSF recipe.

    No galaxy project ID: a PSF from the same *exact* verified observation
    and star sample can be reused across project contexts, but the scientific
    applicability of global/spatial/local PSF at another target position
    still needs separate held-out-star and spatial-representativity QA.
    """
    observation_id: str
    instrument: str
    band: str
    source_sha256: str
    star_selection_sha256: str
    cutout_sky_sha256: str
    recipe_sha256: str
    image_kind: str = "coadd"
    model_scope: str = "global"
    reference_icrs_deg: tuple[float, float] | None = None

    def __post_init__(self):
        if any(not isinstance(s, str) or not _SAFE_NAME.fullmatch(s)
               for s in (self.observation_id, self.instrument)):
            raise ValueError("Observation and instrument need safe explicit IDs")
        if self.band not in ("g", "r", "i", "z"):
            raise ValueError("PSF lineage currently supports optical g/r/i/z only")
        if self.image_kind not in ("coadd", "ccd"):
            raise ValueError("PSF image kind must distinguish coadd from CCD")
        if self.model_scope not in ("global", "local", "spatial"):
            raise ValueError("Unknown PSF spatial scope")
        if any(not isinstance(x, str) or not _HEX_SHA.fullmatch(x) for x in
               (self.source_sha256, self.star_selection_sha256,
                self.cutout_sky_sha256, self.recipe_sha256)):
            raise ValueError("PSF source/selection/sky/recipe need verified SHA-256 IDs")
        pos = self.reference_icrs_deg
        if self.model_scope == "local":
            if pos is None or len(pos) != 2:
                raise ValueError("A local PSF needs its ICRS evaluation position")
        elif pos is not None:
            raise ValueError("Global/spatial PSF models are keyed by dataset, not a galaxy")
        if pos is not None:
            ra, dec = pos
            if (not all(isinstance(x, (int, float)) and math.isfinite(x)
                        for x in pos) or not 0 <= ra < 360 or not -90 <= dec <= 90):
                raise ValueError("Invalid PSF reference ICRS coordinates")
            object.__setattr__(self, "reference_icrs_deg", (float(ra), float(dec)))

    @property
    def digest(self) -> str:
        payload = {
            "schema": "desidera.psf-lineage/1",
            "observation_id": self.observation_id,
            "instrument": self.instrument,
            "band": self.band,
            "source_sha256": self.source_sha256,
            "star_selection_sha256": self.star_selection_sha256,
            "cutout_sky_sha256": self.cutout_sky_sha256,
            "recipe_sha256": self.recipe_sha256,
            "image_kind": self.image_kind,
            "model_scope": self.model_scope,
            "reference_icrs_deg": self.reference_icrs_deg,
        }
        return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                 allow_nan=False).encode("utf-8")).hexdigest()


# Binding requirement for ALL future scientific galaxy photometry:
# every requested optical data set has its OWN star-derived, scientifically
# validated empirical PSF. PSFEx/reference and PSFSIZE are not substitutes.
# Current PSF-0 diagnostic previews are never science receipts.
_REQUIRED_PSF_QA = (
    "independent_stellar_validation",
    "extended_wings",
    "spatial_representativity",
    "sky_wing_degeneracy",
    "coadd_noise_covariance",
)


def inspect_empirical_psf_coverage(
    required_dataset_ids: Mapping[str, str],
    trusted_science_psf_receipts: Mapping[str, Mapping] | None,
) -> dict:
    """Fail-closed readiness contract for a future photometric executor.

    The second argument MUST be derived from internally verified,
    immutable science-adoption receipts, never from an exported preview
    JSON or user-entered metadata. This function does not itself create
    or cryptographically authenticate such receipts. It makes the gate's
    band, provenance and QA conditions explicit before the numerical
    photometry executor is implemented.
    """
    if not isinstance(required_dataset_ids, Mapping) or not required_dataset_ids:
        raise ValueError("Nonempty optical band -> exact dataset mapping required")
    if any(b not in ("g", "r", "i", "z") or not isinstance(ds, str)
           or not ds or not ds.endswith("/" + b)
           for b, ds in required_dataset_ids.items()):
        raise ValueError("Every requested band needs its own exact optical dataset ID")
    if trusted_science_psf_receipts is None:
        trusted_science_psf_receipts = {}
    if not isinstance(trusted_science_psf_receipts, Mapping):
        raise TypeError("Trusted science PSF receipts must be a band mapping")
    by_band = {}
    for band, dataset in required_dataset_ids.items():
        receipt = trusted_science_psf_receipts.get(band)
        reasons = []
        if not isinstance(receipt, Mapping):
            reasons.append("EMPIRICAL_PSF_MISSING")
        else:
            if receipt.get("dataset_id") != dataset or receipt.get("band") != band:
                reasons.append("PSF_DATASET_OR_BAND_MISMATCH")
            if receipt.get("construction") != "EMPIRICAL_FROM_OBSERVED_STARS":
                reasons.append("PSF_NOT_EMPIRICALLY_STAR_DERIVED")
            if (receipt.get("science_adopted") is not True or
                    receipt.get("status") != "SCIENCE_VALIDATED_EMPIRICAL_PSF"):
                reasons.append("PSF_NOT_SCIENTIFICALLY_ADOPTED")
            if receipt.get("source_fits_verified") is not True:
                reasons.append("PSF_SOURCE_PROVENANCE_UNVERIFIED")
            sha = receipt.get("kernel_sha256")
            if not isinstance(sha, str) or _HEX_SHA.fullmatch(sha) is None:
                reasons.append("PSF_KERNEL_HASH_MISSING")
            if not isinstance(receipt.get("gaia_holdout_count"), int) or (
                    receipt.get("gaia_holdout_count", 0) < 3):
                reasons.append("INDEPENDENT_STELLAR_VALIDATION_INSUFFICIENT")
            qa = receipt.get("science_qa")
            if not isinstance(qa, Mapping):
                qa = {}
            for gate in _REQUIRED_PSF_QA:
                if qa.get(gate) is not True:
                    reasons.append("PSF_SCIENCE_QA_MISSING:" + gate)
        by_band[band] = {
            "dataset_id": dataset,
            "ready": not reasons,
            "reasons": reasons,
        }
    complete = all(item["ready"] for item in by_band.values())
    return {
        "schema": "desidera.empirical-psf-all-required-bands/1",
        "status": ("READY_FOR_DOWNSTREAM_PSF_GATE_ONLY"
                   if complete else "BLOCKED_MISSING_VALIDATED_EMPIRICAL_PSF"),
        "ready": complete,
        "band_results": by_band,
        "all_required_bands_must_pass": True,
        "analytic_gaussian_or_single_ccd_psfex_fallback_forbidden": True,
        "psf0_preview_never_counts_as_science_adoption": True,
        "other_photometry_gates_still_required": True,
    }


def require_empirical_psf_coverage(
    required_dataset_ids: Mapping[str, str],
    trusted_science_psf_receipts: Mapping[str, Mapping] | None,
) -> dict:
    """Refuse downstream science photometry if ANY required band is missing.

    This is a gate contract for the as-yet-unimplemented photometry
    executor. A passing receipt does NOT independently approve sky,
    contamination, covariance or SBF readiness.
    """
    report = inspect_empirical_psf_coverage(
        required_dataset_ids, trusted_science_psf_receipts)
    if not report["ready"]:
        failures = "; ".join(
            band + ": " + ", ".join(row["reasons"])
            for band, row in report["band_results"].items()
            if not row["ready"])
        raise ValueError("Scientific photometry blocked: " + failures)
    return report
