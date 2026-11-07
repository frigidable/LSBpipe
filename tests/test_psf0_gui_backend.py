"""PSF-0 GUI contract: reuse saved Leo P galaxy project, no CLI directories."""
from __future__ import annotations

import pytest

from lsbpipe.photometry.psf_gui_backend import inspect_psf0_bands
from lsbpipe.surveys.object_projects import create_object_project, save_object_project
from lsbpipe.surveys.legacy import SkyField
from lsbpipe.surveys.dataset_integrity import _expected_paths


def _leo_project(tmp_path, monkeypatch, *, galaxy="Leo P"):
    monkeypatch.setenv("LSBPIPE_SESSION_DIR", str(tmp_path / "session"))
    project = create_object_project(
        galaxy, tmp_path / "Leo_P",
        field=SkyField(155.438012, 18.088025, 5.))
    project.data_root = tmp_path / "Leo_P" / "originals"
    project.data_root.mkdir()
    project.selected_dataset = ("dr11", "south", "1554p180", "g")
    save_object_project(project)
    return project


def test_psf_gui_lists_r_and_g_independently_from_selected_g(tmp_path, monkeypatch):
    project = _leo_project(tmp_path, monkeypatch)
    rpaths = _expected_paths(project.data_root, ("dr11", "south", "1554p180", "r"))
    for path in rpaths.values():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"NOT A FITS - PATH PRESENCE ONLY")
    original = (project.root / "lsbpipe-object.json").read_bytes()
    info = inspect_psf0_bands(project.root)
    assert info["project_id"] == project.project_id
    assert info["dataset"] == ("dr11", "south", "1554p180", "g")
    bands = {row["band"]: row for row in info["bands"]}
    assert bands["r"]["paths_present_unverified"] is True
    assert bands["r"]["selected"] is False
    assert bands["g"]["selected"] is True
    assert bands["g"]["present_count"] == 1  # shared maskbits only
    assert bands["r"]["present_count"] == 4
    assert bands["r"]["files"] == {key: True for key in rpaths}
    assert info["status"] == "PATHS_ONLY_NOT_SCIENCE_VALIDATED"
    assert (project.root / "lsbpipe-object.json").read_bytes() == original
    assert not (project.root / "analyses").exists()


def test_psf_gui_refuses_other_project_without_touching_fits(tmp_path, monkeypatch):
    p = _leo_project(tmp_path, monkeypatch, galaxy="UGC 2162")
    with pytest.raises(ValueError, match="Leo P"):
        inspect_psf0_bands(p.root)


def test_controlled_psf_recipe_variants_have_explicit_science_semantics():
    from lsbpipe.photometry.psf_gui_backend import (
        PSF0_DIAGNOSTIC_EXPERIMENTS, psf0_experiment_recipe)
    assert tuple(PSF0_DIAGNOSTIC_EXPERIMENTS) == (
        "baseline", "outer_sky", "outer_sky_robust",
        "outer_sky_convergence", "outer_sky_no_wing_smoothing",
        "outer_sky_isolation_footprint")
    base = psf0_experiment_recipe(4.8, "baseline")
    extent = psf0_experiment_recipe(4.8, "outer_sky")
    robust = psf0_experiment_recipe(4.8, "outer_sky_robust")
    long = psf0_experiment_recipe(4.8, "outer_sky_convergence")
    no_wing = psf0_experiment_recipe(4.8, "outer_sky_no_wing_smoothing")
    footprint = psf0_experiment_recipe(4.8, "outer_sky_isolation_footprint")
    assert base.cutout_size_pix == 31
    assert base.sky_annulus_inner_pix == 10.
    assert base.sky_annulus_outer_pix == 15.
    assert base.wing_smoothing is True
    assert base.max_iterations == 8
    assert extent.cutout_size_pix == 61
    assert extent.sky_annulus_inner_pix == 22.
    assert extent.sky_annulus_outer_pix == 28.
    assert extent.min_neighbor_separation_pix >= 61.
    assert robust.cutout_size_pix == extent.cutout_size_pix
    assert robust.sky_annulus_inner_pix == extent.sky_annulus_inner_pix
    assert robust.sky_annulus_outer_pix == extent.sky_annulus_outer_pix
    assert robust.min_neighbor_separation_pix == extent.min_neighbor_separation_pix
    assert robust.training_outlier_passes == 2
    assert extent.training_outlier_passes == 0
    # Controlled solver experiment: only the iteration budget changes.
    assert long.max_iterations == 16
    assert extent.max_iterations == 8
    assert long.training_outlier_passes == 0
    from dataclasses import replace
    assert long == replace(extent, max_iterations=16)
    assert no_wing == replace(long, wing_smoothing=False)
    assert no_wing.max_iterations == 16
    assert no_wing.psf_oversampling == 1
    assert no_wing.training_outlier_passes == 0
    assert footprint == replace(long, min_neighbor_separation_pix=float(long.cutout_size_pix))
    assert footprint.min_neighbor_separation_pix == 61.0
    assert len({r.digest for r in (base, extent, robust, long, no_wing, footprint)}) == 6
    with pytest.raises(ValueError, match="Unknown"):
        psf0_experiment_recipe(4.8, "automatically_good_psf")
    assert all(key in PSF0_DIAGNOSTIC_EXPERIMENTS
               for key in ("baseline","outer_sky","outer_sky_robust",
                           "outer_sky_convergence",
                           "outer_sky_no_wing_smoothing",
                           "outer_sky_isolation_footprint"))


def test_psf0_extended_sky_adapts_to_actual_leo_p_psfsize_prior():
    from lsbpipe.photometry.psf_gui_backend import psf0_experiment_recipe
    import pytest
    prior = 6.57  # Real Leo P DR11 g-map FWHM prior (not actual ePSF).
    baseline = psf0_experiment_recipe(prior, "baseline")
    wide = psf0_experiment_recipe(prior, "outer_sky")
    guarded = psf0_experiment_recipe(prior, "outer_sky_robust")
    long = psf0_experiment_recipe(prior, "outer_sky_convergence")
    no_wing = psf0_experiment_recipe(prior, "outer_sky_no_wing_smoothing")
    footprint = psf0_experiment_recipe(prior, "outer_sky_isolation_footprint")
    assert (baseline.cutout_size_pix,
            baseline.sky_annulus_inner_pix,
            baseline.sky_annulus_outer_pix) == (31, 10., 15.)
    assert (wide.cutout_size_pix,
            wide.sky_annulus_inner_pix,
            wide.sky_annulus_outer_pix) == (81, 30., 38.)
    assert wide.sky_annulus_inner_pix / prior >= 4.5
    assert wide.sky_annulus_outer_pix <= wide.cutout_size_pix//2 - 2
    assert wide.min_neighbor_separation_pix >= wide.cutout_size_pix
    assert guarded.cutout_size_pix == wide.cutout_size_pix
    assert guarded.training_outlier_passes == 2
    assert wide.training_outlier_passes == 0
    from dataclasses import replace
    assert long == replace(wide, max_iterations=16)
    assert no_wing == replace(long, wing_smoothing=False)
    assert footprint == replace(long, min_neighbor_separation_pix=81.)
    assert footprint.sky_annulus_outer_pix * 2 <= footprint.min_neighbor_separation_pix
    assert footprint.min_neighbor_separation_pix == footprint.cutout_size_pix
    assert footprint.min_neighbor_separation_pix < long.min_neighbor_separation_pix
    assert wide.digest != psf0_experiment_recipe(4.80, "outer_sky").digest
    # The sky geometry cannot silently fall back to a closer annulus
    # for bad seeing. The scientist must choose a different experiment.
    with pytest.raises(ValueError, match="above the 91 px PSF0 limit"):
        psf0_experiment_recipe(8.5, "outer_sky")
    with pytest.raises(ValueError, match="above the 91 px PSF0 limit"):
        psf0_experiment_recipe(8.5, "outer_sky_robust")
    with pytest.raises(ValueError, match="above the 91 px PSF0 limit"):
        psf0_experiment_recipe(8.5, "outer_sky_convergence")
    with pytest.raises(ValueError, match="above the 91 px PSF0 limit"):
        psf0_experiment_recipe(8.5, "outer_sky_no_wing_smoothing")
    with pytest.raises(ValueError, match="above the 91 px PSF0 limit"):
        psf0_experiment_recipe(8.5, "outer_sky_isolation_footprint")


def test_psf0_scaled_annuli_are_explicit_not_scientifically_certified():
    from lsbpipe.photometry.psf_gui_backend import psf0_experiment_recipe
    for seeing in (2.5, 3.2, 4.8, 5.5, 6.57, 7.2):
        for mode in ("outer_sky", "outer_sky_robust",
                     "outer_sky_convergence",
                     "outer_sky_no_wing_smoothing",
                     "outer_sky_isolation_footprint"):
            recipe = psf0_experiment_recipe(seeing, mode)
            assert recipe.sky_annulus_inner_pix >= 4.5*seeing
            assert recipe.sky_annulus_outer_pix >= 5.75*seeing
            assert recipe.sky_annulus_outer_pix <= recipe.cutout_size_pix//2
            assert recipe.min_neighbor_separation_pix >= recipe.cutout_size_pix
    assert set(("baseline","outer_sky","outer_sky_robust",
                "outer_sky_convergence",
                "outer_sky_no_wing_smoothing",
                "outer_sky_isolation_footprint")) == set(
        __import__(
            "lsbpipe.photometry.psf_gui_backend",
            fromlist=["PSF0_DIAGNOSTIC_EXPERIMENTS"]
        ).PSF0_DIAGNOSTIC_EXPERIMENTS)


def test_psf_source_key_uses_observing_brick_not_saved_default_band():
    from lsbpipe.photometry.psf_gui_backend import _same_observation_source
    source = ("dr11", "south", "1554p180", "g")
    # An r-band PSF can be analyzed while the project default is g.
    assert _same_observation_source(source, ("dr11","south","1554p180","r"))
    assert _same_observation_source(source, source)
    for other in (
        ("dr9","south","1554p180","g"),
        ("dr11","north","1554p180","g"),
        ("dr11","south","1555p180","g"),
        ("dr11","south","1554p180"),
        None,
    ):
        assert not _same_observation_source(source, other)


def test_tractor_psf_training_keeps_non_gaia_identity_and_audits_every_gate():
    import numpy as np
    from lsbpipe.photometry.psf_empirical import PSF0Config
    from lsbpipe.photometry.psf_tractor_gaia import TractorGaiaMatcher

    def source(objid, ref_cat, ref_id, *, kind="PSF"):
        return {
            "ref_cat": ref_cat, "ref_id": ref_id, "objid": objid,
            "type": kind, "fitbits": 0, "maskbits": 0,
            "flux": 30., "flux_ivar": 1.,
            "gaia_duplicated_source": False, "gaia_g_mag": 18.,
        }

    matcher = TractorGaiaMatcher(
        source_path="original-tractor-test.fits",
        source_sha256="b"*64,
        dataset_id="dr11/south/1554p180/r",
        rows=(source(1, "G3", 999),
              source(2, "", -1),
              source(3, "", -1, kind="REX")),
        pixel_positions=np.array([[10., 10.], [110., 110.],
                                  [210., 210.]]))
    gaia, failure, audit = matcher.match(
        10., 10., band="r", require_gaia=True, return_audit=True)
    assert failure is None
    assert gaia["gaia_source_id"] == 999
    assert audit["gaia_confirmed"] is True
    strict, reason, strict_audit = matcher.match(
        110., 110., band="r", require_gaia=True, return_audit=True)
    assert strict is None
    assert reason == strict_audit["first_failure"] == "NOT_GAIA_DR3"
    vetted, failure, trace = matcher.match(
        110., 110., band="r", require_gaia=False, return_audit=True)
    assert failure is None
    assert vetted["gaia_source_id"] is None
    assert vetted["gaia_confirmed"] is False
    assert trace["gaia_confirmed"] is False
    assert trace["tractor_band_snr_diagonal_only"] == 30.
    assert trace["checks_passed"][-1] == "no_significant_tractor_neighbors"
    invalid, failure, _ = matcher.match(
        210., 210., band="r", require_gaia=False, return_audit=True)
    assert invalid is None
    assert failure == "TRACTOR_NONSTELLAR_MODEL"
    assert PSF0Config(star_catalog_policy="tractor_psf_validated").star_catalog_policy == (
        "tractor_psf_validated")
