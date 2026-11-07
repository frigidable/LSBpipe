"""Every required science band needs its OWN validated stellar empirical PSF.

This tests the future photometry gate contract, not a fabricated science
result or a bypass around the current PSF-0 NOT_ADOPTED state.
"""
import copy
import pytest

from lsbpipe.photometry.workflow_dependencies import (
    inspect_empirical_psf_coverage,
    require_empirical_psf_coverage,
)


DATASETS = {band: f"dr11/south/1554p180/{band}" for band in "griz"}


def science_receipt(band):
    # Synthetic *unit-test metadata only*, never an actual adoption receipt.
    return {
        "dataset_id": DATASETS[band],
        "band": band,
        "construction": "EMPIRICAL_FROM_OBSERVED_STARS",
        "science_adopted": True,
        "status": "SCIENCE_VALIDATED_EMPIRICAL_PSF",
        "source_fits_verified": True,
        "kernel_sha256": "a" * 64,
        "gaia_holdout_count": 4,
        "science_qa": {
            "independent_stellar_validation": True,
            "extended_wings": True,
            "spatial_representativity": True,
            "sky_wing_degeneracy": True,
            "coadd_noise_covariance": True,
        },
    }


def test_missing_g_blocks_all_band_photometry_even_when_riz_have_receipts():
    partial = {b: science_receipt(b) for b in "riz"}
    with pytest.raises(ValueError, match="g: EMPIRICAL_PSF_MISSING"):
        require_empirical_psf_coverage(DATASETS, partial)
    report = inspect_empirical_psf_coverage(DATASETS, partial)
    assert report["ready"] is False
    assert report["band_results"]["g"]["ready"] is False


def test_current_psf0_experimental_preview_is_never_science_ready():
    existing = science_receipt("g")
    existing["science_adopted"] = False
    existing["status"] = "EXPERIMENTAL_PSF_NOT_ADOPTED"
    report = inspect_empirical_psf_coverage(DATASETS, {"g": existing})
    assert "PSF_NOT_SCIENTIFICALLY_ADOPTED" in report["band_results"]["g"]["reasons"]
    assert report["ready"] is False


@pytest.mark.parametrize("construction", (
    "PSFEX_SINGLE_CCD",
    "GAUSSIAN_FROM_PSF_SIZE",
    "ANALYTIC_MOFFAT",
    "TRACTOR_REFERENCE_ONLY",
))
def test_non_empirical_fallback_never_satisfies_science_gate(construction):
    record = science_receipt("g")
    record["construction"] = construction
    with pytest.raises(ValueError, match="PSF_NOT_EMPIRICALLY_STAR_DERIVED"):
        require_empirical_psf_coverage({"g": DATASETS["g"]}, {"g": record})


def test_science_gate_checks_exact_data_identity_and_every_quality_flag():
    record = science_receipt("g")
    record["dataset_id"] = "dr11/south/1554p180/r"
    with pytest.raises(ValueError, match="PSF_DATASET_OR_BAND_MISMATCH"):
        require_empirical_psf_coverage({"g": DATASETS["g"]}, {"g": record})
    for key in science_receipt("g")["science_qa"]:
        bad = science_receipt("g")
        bad["science_qa"][key] = False
        with pytest.raises(ValueError, match="PSF_SCIENCE_QA_MISSING:" + key):
            require_empirical_psf_coverage({"g": DATASETS["g"]}, {"g": bad})


def test_all_band_receipts_can_pass_readiness_contract_only():
    full = {b: science_receipt(b) for b in "griz"}
    report = require_empirical_psf_coverage(DATASETS, full)
    assert report["ready"] is True
    assert report["other_photometry_gates_still_required"] is True
    assert report["psf0_preview_never_counts_as_science_adoption"] is True


def test_invalid_input_or_unverified_provenance_rejected():
    with pytest.raises(ValueError, match="Nonempty"):
        require_empirical_psf_coverage({}, {})
    with pytest.raises(ValueError, match="exact optical dataset"):
        require_empirical_psf_coverage({"g": DATASETS["r"]}, {})
    with pytest.raises(TypeError, match="band mapping"):
        require_empirical_psf_coverage({"g": DATASETS["g"]}, ["not a receipt"])
    for field, value, flag in (
        ("source_fits_verified", False, "PSF_SOURCE_PROVENANCE_UNVERIFIED"),
        ("kernel_sha256", None, "PSF_KERNEL_HASH_MISSING"),
        ("gaia_holdout_count", 0, "INDEPENDENT_STELLAR_VALIDATION_INSUFFICIENT"),
    ):
        bad = science_receipt("g")
        bad[field] = value
        with pytest.raises(ValueError, match=flag):
            require_empirical_psf_coverage({"g": DATASETS["g"]}, {"g": bad})
