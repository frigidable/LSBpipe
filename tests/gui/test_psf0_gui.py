"""Offline GUI PSF-0 navigation and scientifically rejected Leo P pilot."""
from __future__ import annotations

import pytest
import numpy as np
pytest.importorskip("pytestqt")
pytest.importorskip("PySide6.QtWebEngineWidgets")

from lsbpipe.gui.survey_explorer import SurveyExplorer
from lsbpipe.gui.psf0_panel import PSF0Panel
from PySide6.QtWidgets import QApplication, QWidget
from lsbpipe.photometry.psf_signed_qa import signed_psf_qa
from lsbpipe.surveys.object_projects import create_object_project, save_object_project
from lsbpipe.surveys.dataset_integrity import _expected_paths
from lsbpipe.surveys.legacy import SkyField
from scripts.offline_gui_fixture import offline_legacy_viewer


def test_psf_gui_uses_existing_project_and_chooses_present_r_instead_of_saved_g(
        qtbot, tmp_path, monkeypatch):
    monkeypatch.setenv("LSBPIPE_SESSION_DIR", str(tmp_path / "session"))
    project = create_object_project(
        "Leo P", tmp_path / "Leo_P",
        field=SkyField(155.438012, 18.088025, 5.))
    with offline_legacy_viewer():
        window = SurveyExplorer(restore_session=False, launch_remote=False)
    qtbot.addWidget(window)
    window.attach_object_project(project)
    window.show()

    # Attach local inventory AFTER opening the window, avoiding any
    # legacy auto-index attempt on intentionally non-FITS test placeholders.
    project.data_root = tmp_path / "Leo_P" / "originals"
    project.data_root.mkdir()
    project.selected_dataset = ("dr11", "south", "1554p180", "g")
    save_object_project(project)
    paths = _expected_paths(project.data_root, ("dr11", "south", "1554p180", "r"))
    for p in paths.values():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"not a FITS; UI existence-only fixture")

    configurations = []

    def fake_backend(root, *, band, config, protection_arcsec, progress=None):
        configurations.append(config)
        if progress is not None:
            progress((0, 1, "Synthetic QA-rejected star sample"))
        assert root == str(project.root)
        assert band == "r"
        assert config.guessed_fwhm_pix == pytest.approx(4.8)
        assert protection_arcsec == pytest.approx(90.)
        signed = np.zeros((21,21), float)
        signed[10,10] = 1.
        signed[10,15] = -(.075/.925)
        signed_qa = signed_psf_qa(signed, 4.8)
        return {
            "project_root": root, "project_id": project.project_id,
            "dataset": project.selected_dataset,
            "review_band": band,
            "status": "PSF0_QUALITY_REJECTED_NOT_ADOPTED",
            "reason": "Strong negative EPSF ringing: 7.50% exceeds 2.50%",
            "diagnostics": {
                "negative_absolute_mass_fraction": signed_qa["negative_absolute_mass_fraction"],
                "blocking_threshold_fraction": .025,
                "signed_kernel_qa": signed_qa,
                "signed_kernel_values_for_qa_only": signed.tolist(),
                "n_train": 10, "n_holdout": 3,
                "training": [{"x_pix": 20., "y_pix": 30.,
                              "cutout_sky_nMgy": .2,
                              "gradient_sigma_span": .7}],
                "holdout": [],
                "rejected_reasons": {"NEIGHBOR_WITHIN_ISOLATION_RADIUS": 2},
                "notes": ["Not a science PSF"],
            },
        }

    import lsbpipe.photometry.psf_gui_backend as backend
    monkeypatch.setattr(backend, "run_gui_psf0", fake_backend)
    window.photometry_btn.click()
    panel = window.photometry_workspace.psf0_panel
    assert window.preview_tabs.currentWidget() is window.photometry_workspace
    assert panel.run_btn.isEnabled()
    assert panel.experiment.count() == 6
    assert tuple(window.photometry_workspace.psf0_panels) == ("g", "r", "i", "z")
    assert window.photometry_workspace.psf0_panels["r"] is panel
    assert panel.experiment.currentData() == "baseline"
    assert panel.band.currentData() == "r"
    assert panel.protection.value() == pytest.approx(90.)
    assert not panel.save_btn.isEnabled()
    before = {key:path.read_bytes() for key,path in paths.items()}
    panel.run_btn.click()
    qtbot.waitUntil(lambda: window.thread is None, timeout=15000)
    assert "REJECTED" in panel.status.text()
    assert "7.50%" in panel.summary.text()
    assert "Strong negative EPSF ringing" in panel.qa.toPlainText()
    assert panel.stars.rowCount() == 1
    assert panel.stars.columnCount() == 6
    assert panel.stars.horizontalHeaderItem(0).text() == "Star ID"
    assert panel.kernel_view.pixmap() is not None
    assert not panel.kernel_view.pixmap().isNull()
    assert "Intermediate" in panel.qa.toPlainText()
    assert panel.copy_qa_btn.isEnabled()
    panel.copy_qa_btn.click()
    copied = QApplication.clipboard().text()
    assert "PSF0_QUALITY_REJECTED_NOT_ADOPTED" in copied
    assert "signed_kernel_qa" in copied
    assert "signed_kernel_values_for_qa_only" not in copied
    assert '"comparison_trials_not_adopted"' in copied
    assert "baseline: neg 7.50%" in panel.compare.text()
    assert not panel.save_btn.isEnabled()
    # A second reconstruction can change the outer-sky annulus while the
    # historical baseline remains visible and traceable as non-adopted.
    panel.experiment.setCurrentIndex(panel.experiment.findData("outer_sky"))
    assert not panel.copy_qa_btn.isEnabled()
    assert "baseline: neg 7.50%" in panel.compare.text()
    panel.run_btn.click()
    qtbot.waitUntil(lambda: window.thread is None, timeout=15000)
    assert len(configurations) == 2
    assert configurations[-1].cutout_size_pix == 61
    assert configurations[-1].sky_annulus_inner_pix == 22.
    assert "outer_sky: neg 7.50%" in panel.compare.text()
    assert "baseline: neg 7.50%" in panel.compare.text()
    assert panel.copy_qa_btn.isEnabled()
    panel.copy_qa_btn.click()
    copied_comparison = QApplication.clipboard().text()
    assert '"experiment": "baseline"' in copied_comparison
    assert '"experiment": "outer_sky"' in copied_comparison
    # Changing the recipe must invalidate rejection evidence, not just a
    # successful result, to prevent accidental cross-recipe reporting.
    panel.fwhm.setValue(5.5)
    assert "baseline: neg" not in panel.compare.text()
    assert "outer_sky: neg" not in panel.compare.text()
    assert not panel.copy_qa_btn.isEnabled()
    assert not (project.root / "analyses" / "psf-pilots").exists()
    assert all(paths[k].read_bytes() == data for k,data in before.items())
    assert project.selected_dataset == ("dr11", "south", "1554p180", "g")
    window._returning_to_hub = True
    window.close()


def test_standalone_panel_has_no_results_or_actions_without_project(qtbot):
    panel = PSF0Panel()
    qtbot.addWidget(panel)
    panel.show()
    assert not panel.run_btn.isEnabled()
    assert not panel.save_btn.isEnabled()
    assert panel.active_preview is None
    assert not panel.copy_qa_btn.isEnabled()


def test_star_score_history_is_not_dumped_into_on_screen_catalog_summary():
    from lsbpipe.gui.psf0_panel import PSF0Panel
    compact=PSF0Panel._catalog_screening_summary({
        "policy":"gaia_strict",
        "catalog_source_sha256":"a"*64,
        "training_residual_screening_history":[{
            "screened_stars":[{"star_id":"star-0001",
                              "score":20.,"role":"train"}]}],
        "automatic_training_rejections":["star-0001"],
        "brightness_score_coupling_blocked_auto_trim":True,
    })
    assert "gaia_strict" in compact
    assert "brightness_score_coupling_blocked_auto_trim" in compact
    assert "screened_stars" not in compact
    assert "star-0001" not in compact


def test_gui_psfsize_suggestion_uses_real_provenance_and_updates_spinbox(qtbot):
    from pathlib import Path
    panel = PSF0Panel()
    qtbot.addWidget(panel)
    panel._project_root = Path("C:/Leo_P")
    panel.band.addItem("g · native", "g")
    result = {
        "project_root":str(panel._project_root), "band":"g",
        "seeing":{
            "available":True,
            "source":"LOCAL_DR11_TRACTOR_NEARBY_PSF_SIZE_NOT_PIXEL_MAP",
            "path":"C:/Leo_P/dr11/south/tractor/155/tractor-1554p180.fits",
            "sample":"median of 40 local BRICK_PRIMARY PSFSIZE_G entries",
            "psfsize_arcsec":1.2, "image_pixel_scale_arcsec":.262,
            "suggested_detection_fwhm_pix":1.2/.262,
            "native_map_status":"Native PSFSIZE map rejected: WCS mismatch",
        },
    }
    panel.show_fwhm_suggestion(result)
    assert panel.fwhm.value() == pytest.approx(4.58)
    assert "Tractor" in panel.prior_source.text()
    assert "WCS mismatch" in panel.prior_source.text()
    assert "4.58 px" in panel.status.text()
    assert "Not an empirical ePSF" in panel.prior_source.text()


def test_failed_psfsize_preserves_manual_guess_with_visible_reason(qtbot):
    from pathlib import Path
    panel=PSF0Panel()
    qtbot.addWidget(panel)
    panel._project_root=Path("C:/Leo_P")
    panel.band.addItem("g", "g")
    panel.fwhm.setValue(5.25)
    panel.show_fwhm_suggestion({
        "project_root":str(panel._project_root),"band":"g",
        "seeing":{"available":False,
                  "source":"NO_VALID_LOCAL_DR11_PSF_SIZE_PRIOR",
                  "message":"Expected native PSFSIZE file not found"},
    })
    assert panel.fwhm.value()==pytest.approx(5.25)
    assert "Expected native PSFSIZE file not found" in (
        panel.prior_source.text())


def test_gui_stellar_sample_underflow_shows_counts_without_signed_psf(
        qtbot, tmp_path, monkeypatch):
    """Reject too-small samples visibly and retain full serializable QA."""
    from lsbpipe.gui.psf0_full_qa import detailed_band_report

    monkeypatch.setenv("LSBPIPE_SESSION_DIR", str(tmp_path / "session"))
    project = create_object_project(
        "Leo P", tmp_path / "Leo_P",
        field=SkyField(155.438012, 18.088025, 5.))
    project.data_root = tmp_path / "Leo_P" / "originals"
    project.data_root.mkdir()
    project.selected_dataset = ("dr11", "south", "1554p180", "r")
    save_object_project(project)
    paths = _expected_paths(project.data_root, project.selected_dataset)
    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"placeholder for GUI metadata; no FITS read")
    panel = PSF0Panel(fixed_band="r")
    qtbot.addWidget(panel)
    panel.bind_project(project.root)
    assert panel.band.currentData() == "r"
    payload = {
        "project_root": str(project.root), "project_id": project.project_id,
        "dataset": project.selected_dataset, "review_band": "r",
        "status": "PSF0_QUALITY_REJECTED_NOT_ADOPTED",
        "reason": "Insufficient isolated, unsaturated, sky-screened stars: 10 (minimum 13)",
        "diagnostics": {
            "stage": "stellar_sample_underflow",
            "n_detected_candidates": 35,
            "n_screened_candidates": 35,
            "n_accepted_before_width_screen": 12,
            "n_accepted_stars": 10,
            "minimum_accepted_stars": 13,
            "min_train_stars": 10, "min_holdout_stars": 3,
            "rejected_reasons": {"NEIGHBOR_WITHIN_ISOLATION_RADIUS": 15,
                                 "GAIA_STELLAR_CORE_SIZE_OUTLIER": 2},
            "accepted_candidates": [
                {"star_id": "star-0001", "x_pix": 35., "y_pix": 78.,
                 "ra_deg": 155.44, "dec_deg": 18.08,
                 "cutout_sky_nMgy": 1.2, "gradient_sigma_span": .7}],
            "catalog_selection_qa": {"policy": "gaia_strict"},
            "notes": ["No PSF was fitted"],
        },
    }
    panel.show_quality_rejection(payload)
    assert "10 accepted / 13 required" in panel.summary.text()
    assert "Detected candidates: 35" in panel.qa.toPlainText()
    assert "NEIGHBOR_WITHIN_ISOLATION_RADIUS" in panel.qa.toPlainText()
    assert "No ePSF was fitted" in panel.qa.toPlainText()
    assert "no ePSF was fitted" in panel.model_caption.text()
    assert panel.kernel_view.pixmap() is None or panel.kernel_view.pixmap().isNull()
    assert panel.profile_view.pixmap() is None or panel.profile_view.pixmap().isNull()
    assert panel.kernel_view.height() == 56
    assert panel.profile_view.height() == 56
    assert panel.stars.rowCount() == 1
    assert panel.stars.item(0, 1).text() == "candidate"
    assert not panel.save_btn.isEnabled()
    report = detailed_band_report(panel)
    assert report["science_report_or_rejection"]["diagnostics"]["stage"] == (
        "stellar_sample_underflow")
    assert report["science_report_or_rejection"]["diagnostics"][
        "n_accepted_stars"] == 10
    assert all(path.read_bytes() == b"placeholder for GUI metadata; no FITS read"
               for path in paths.values())


def test_tractor_vetted_gui_mode_requires_exact_native_catalogue(qtbot, tmp_path, monkeypatch):
    """Pilot policy cannot silently fall back to DAO without its Tractor file."""
    from lsbpipe.photometry.psf_tractor_gaia import tractor_catalog_location

    monkeypatch.setenv("LSBPIPE_SESSION_DIR", str(tmp_path / "session"))
    project = create_object_project(
        "Leo P", tmp_path / "Leo_P",
        field=SkyField(155.438012, 18.088025, 5.))
    project.data_root = tmp_path / "Leo_P" / "originals"
    project.data_root.mkdir()
    project.selected_dataset = ("dr11", "south", "1554p180", "r")
    save_object_project(project)
    paths = _expected_paths(project.data_root, project.selected_dataset)
    for p in paths.values():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"offline test source path only")
    panel = PSF0Panel(fixed_band="r")
    qtbot.addWidget(panel)
    panel.bind_project(project.root)
    idx = panel.catalog_policy.findData("tractor_psf_validated")
    assert idx >= 0
    panel.catalog_policy.setCurrentIndex(idx)
    assert not panel.run_btn.isEnabled()
    tractor_path, _ = tractor_catalog_location(
        project.data_root, release="dr11", region="south", brick="1554p180")
    tractor_path.parent.mkdir(parents=True, exist_ok=True)
    tractor_path.write_bytes(b"original exact-brick presence not verified")
    panel.refresh_sources()
    panel.catalog_policy.setCurrentIndex(
        panel.catalog_policy.findData("tractor_psf_validated"))
    assert panel.run_btn.isEnabled()
    request = panel.make_run_request()
    assert request["star_catalog_policy"] == "tractor_psf_validated"
    assert request["band"] == "r"
    assert panel.active_preview is None


def test_photometry_compact_band_tabs_and_on_demand_diagnostics(qtbot):
    """One main band surface instead of four stacked full scientific reports."""
    from lsbpipe.gui.photometry_workspace import PhotometryWorkspace

    workspace = PhotometryWorkspace()
    qtbot.addWidget(workspace)
    workspace.show()
    tabs = workspace.band_tabs
    assert tabs.objectName() == "psf0_single_band_tabs"
    assert tabs.count() == 4
    assert [tabs.tabText(i) for i in range(tabs.count())] == [
        "G · No FITS", "R · No FITS", "I · No FITS", "Z · No FITS"]
    assert tuple(workspace.psf0_panels) == ("g", "r", "i", "z")
    assert tabs.currentWidget() is workspace.psf0_panels["g"]
    assert workspace.band_selector.count() == 4
    assert workspace.band_selector.isVisible()
    assert not tabs.tabBar().isVisible()
    assert [workspace.band_selector.tabText(i)
            for i in range(4)] == [
        "G · No FITS", "R · No FITS", "I · No FITS", "Z · No FITS"]
    assert not workspace.workflow_details.isVisible()
    assert not workspace.psf0_panels["g"].inspection_details.isVisible()
    assert not workspace.psf0_panels["g"].advanced_details.isVisible()
    assert workspace.psf0_panels["g"].kernel_view.isVisible()
    assert workspace.psf0_panels["g"].profile_view.isVisible()
    assert not workspace.psf0_panels["g"].kernel_view.pixmap()
    assert not workspace.psf0_panels["g"].profile_view.pixmap()
    assert workspace.source_details_toggle.isCheckable()
    assert workspace.method_details_toggle.isCheckable()

    # Scientific configuration of an inactive band must not be reset.
    g = workspace.psf0_panels["g"]
    r = workspace.psf0_panels["r"]
    g.fwhm.setValue(5.75)
    g.inspection_toggle.click()
    assert g.inspection_details.isVisible()
    assert g.qa.isVisible()
    assert g.stars.isVisible()
    workspace.view_mode.setCurrentIndex(
        workspace.view_mode.findData("research"))
    g.advanced_toggle.click()
    assert g.advanced_details.isVisible()

    tabs.setCurrentIndex(1)
    assert workspace.band_selector.currentIndex() == 1
    assert tabs.currentWidget() is r
    assert not g.isVisible()
    assert r.isVisible()
    assert not r.inspection_details.isVisible()
    workspace.band_selector.setCurrentIndex(3)
    assert tabs.currentIndex() == 3
    tabs.setCurrentIndex(0)
    assert workspace.band_selector.currentIndex() == 0
    assert g.fwhm.value() == pytest.approx(5.75)
    assert g.inspection_details.isVisible()
    assert g.advanced_details.isVisible()

    workspace.workflow_toggle.click()
    assert workspace.workflow_details.isVisible()
    workspace.source_details_toggle.click()
    assert workspace.source_details_toggle.isChecked()
    workspace.method_details_toggle.click()
    assert workspace.method_details_toggle.isChecked()
    # No mock project/FITS is required; these are presentation-only controls.


def test_guided_vs_research_changes_visibility_not_recipe(qtbot):
    """Mode switching must preserve per-band recipes, FWHM and QA."""
    from lsbpipe.gui.photometry_workspace import PhotometryWorkspace

    workspace = PhotometryWorkspace()
    qtbot.addWidget(workspace)
    workspace.resize(1400, 850)
    workspace.show()
    g = workspace.psf0_panels["g"]
    r = workspace.psf0_panels["r"]
    assert workspace.view_mode.currentData() == "guided"
    assert not g.protection.isVisible()
    assert not g.review_btn.isVisible()
    assert not g.save_btn.isVisible()
    assert not workspace.copy_full_qa_btn.isVisible()
    assert g.fwhm.isVisible()
    assert "target protected" in g.mode_context.text()
    # Native project selection is absent, but the recipe is still editable.
    g.fwhm.setValue(6.25)
    g.protection.setValue(123.)
    g.catalog_policy.setCurrentIndex(
        g.catalog_policy.findData("tractor_psf_validated"))
    g.experiment.setCurrentIndex(g.experiment.findData("outer_sky"))
    request = (g.fwhm.value(), g.protection.value(),
               g.catalog_policy.currentData(), g.experiment.currentData())

    workspace.view_mode.setCurrentIndex(
        workspace.view_mode.findData("research"))
    assert g.protection.isVisible()
    assert g.review_btn.isVisible()
    assert g.save_btn.isVisible()
    assert workspace.copy_full_qa_btn.isVisible()
    g.advanced_toggle.click()
    assert g.advanced_details.isVisible()
    assert g.mode_context.text().startswith("Research controls visible")
    assert (g.fwhm.value(), g.protection.value(),
            g.catalog_policy.currentData(), g.experiment.currentData()) == request
    workspace.band_tabs.setCurrentWidget(r)
    workspace.view_mode.setCurrentIndex(
        workspace.view_mode.findData("guided"))
    workspace.band_tabs.setCurrentWidget(g)
    assert not g.protection.isVisible()
    assert not g.advanced_details.isVisible()
    assert "123.0 arcsec" in g.mode_context.text()
    assert (g.fwhm.value(), g.protection.value(),
            g.catalog_policy.currentData(), g.experiment.currentData()) == request


def test_photometry_preserves_sidebar_and_original_survey_navigation(
        qtbot, tmp_path, monkeypatch):
    """Project search/sidebar remains available across science workspaces."""
    monkeypatch.setenv("LSBPIPE_SESSION_DIR", str(tmp_path / "session"))
    project = create_object_project(
        "Leo P", tmp_path / "Leo_P",
        field=SkyField(155.438012, 18.088025, 5.))
    with offline_legacy_viewer():
        window = SurveyExplorer(restore_session=False, launch_remote=False)
    qtbot.addWidget(window)
    window.attach_object_project(project)
    window.resize(1450, 850)
    window.show()
    assert window.navigation_scroll.isVisible()
    assert window.preview_tabs.tabBar().isVisible()
    window.photometry_btn.click()
    assert window.preview_tabs.currentWidget() is window.photometry_workspace
    assert window.navigation_scroll.isVisible()
    assert window.preview_tabs.tabBar().isVisible()
    assert not window.preview_label.isVisible()  # redundant heading only
    assert window.navigation_scroll.width() >= 275
    assert window.photometry_workspace.title.isVisible()
    assert window.photometry_workspace.band_tabs.isVisible()
    assert window.photometry_workspace.findChild(
        QWidget, "photometry_narrow_science_column").maximumWidth() == 1400
    window.photometry_workspace.back_btn.click()
    assert window.preview_tabs.currentWidget() is not window.photometry_workspace
    assert window.navigation_scroll.isVisible()
    assert window.preview_tabs.tabBar().isVisible()
    assert window.preview_label.isVisible()
    assert project.field.ra_deg == pytest.approx(155.438012)


def test_photometry_fills_normal_desktop_and_hides_empty_psf_footprint(
        qtbot):
    """Preserve readable controls without wasting desktop width or height."""
    from lsbpipe.gui.photometry_workspace import PhotometryWorkspace

    w = PhotometryWorkspace()
    qtbot.addWidget(w)
    w.resize(1920, 1080)
    w.show()
    QApplication.processEvents()
    column = w.findChild(QWidget, "photometry_narrow_science_column")
    assert column is not None
    assert column.maximumWidth() == 1400
    assert column.width() <= 1400
    assert column.width() >= 1300
    assert w.workflow_toggle.maximumWidth() == 325
    assert w.source_details_toggle.maximumWidth() == 390
    assert w.band_tabs.maximumWidth() >= 1400
    for band, panel in w.psf0_panels.items():
        assert panel.kernel_view.size().width() == 240
        assert panel.kernel_view.size().height() == 56
        assert panel.profile_view.height() == 56
        assert not panel.kernel_view.isHidden()
        assert not panel.profile_view.isHidden()
        assert panel._science_row is not None
        assert panel._result_column.objectName() == "psf0_result_column"
        assert panel._controls_column.objectName() == "psf0_controls_column"
        assert panel.catalog_policy.maximumWidth() == 570
        assert panel.experiment.maximumWidth() == 570
        assert panel.fwhm.maximumWidth() == 160
        assert panel.auto_fwhm_btn.maximumWidth() == 230
        assert panel.inspection_toggle.maximumWidth() == 430

    from PySide6.QtWidgets import QScrollArea
    scroll = next(
        area for area in w.findChildren(QScrollArea)
        if area.widget() is column)
    assert scroll.horizontalScrollBar().maximum() == 0
    w.resize(1050, 800)
    qtbot.wait(50)
    assert column.width() <= 1050
    assert scroll.horizontalScrollBar().maximum() == 0
    from PySide6.QtWidgets import QBoxLayout
    assert w.psf0_panels["g"]._science_row.direction() in (
        QBoxLayout.LeftToRight, QBoxLayout.TopToBottom)
    w.resize(760, 750)
    qtbot.wait(50)
    assert column.width() <= 760
    assert scroll.horizontalScrollBar().maximum() == 0
    assert w.psf0_panels["g"]._science_row.direction() == (
        QBoxLayout.TopToBottom)


def test_real_star_median_profile_is_visible_and_not_fabricated(qtbot):
    """Plot native image/model arrays, exclude non-training review sources."""
    from lsbpipe.gui.psf_star_review import ensemble_training_radial_plot
    from lsbpipe.gui.psf0_panel import PSF0Panel

    panel = PSF0Panel(fixed_band="r")
    qtbot.addWidget(panel)
    panel.resize(1400, 680)
    panel.show()
    # Pure synthetic test arrays model the shape of a real worker snapshot;
    # production GUI only receives its source/mask/model from the worker.
    y, x = np.mgrid[:31, :31]
    model = np.exp(-((x-15.2)**2+(y-14.8)**2)/25.)
    obs = model*(1.+.05*np.sin(x*.45))
    sample = {
        "star_id": "train-1",
        "role": "train",
        "cutout": obs,
        "model": model,
        "valid": np.ones((31,31), bool),
        "center_in_cutout": (15.2,14.8),
        "peak_nMgy": 1.,
    }
    snapshot = {
        "schema": "desidera.psf0-star-review-in-memory/1",
        "stars": [sample, {**sample, "star_id": "excluded",
                             "role": "excluded"}],
    }
    panel._star_review_data = snapshot
    panel._show_real_radial_profile()
    pixmap = panel.profile_view.pixmap()
    assert pixmap is not None and not pixmap.isNull()
    assert pixmap.width() > 300
    assert "N=1" in panel.profile_caption.text()
    assert "NOT independent holdout" in panel.profile_caption.text()
    assert ensemble_training_radial_plot({
        "schema": "desidera.psf0-star-review-in-memory/1",
        "stars": [{**sample, "role": "excluded"}],
    }) is None
    panel._clear_science_visuals("Underflow: no fitted model.")
    assert panel.kernel_view.isVisible()
    assert panel.profile_view.isVisible()
    assert panel.kernel_view.pixmap() is None or panel.kernel_view.pixmap().isNull()
    assert panel.profile_view.pixmap() is None or panel.profile_view.pixmap().isNull()
    assert "Underflow" in panel.model_caption.text()


def test_radial_plot_fits_entire_label_without_clipping_on_resize(qtbot):
    """The source's scientific axes, labels and residuals must stay in view."""
    from PySide6.QtGui import QPixmap, QColor
    from lsbpipe.gui.psf0_panel import _AspectFitProfileLabel

    label = _AspectFitProfileLabel()
    qtbot.addWidget(label)
    label.setFixedHeight(278)
    label.resize(450, 278)
    label.show()
    src = QPixmap(540, 315)
    src.fill(QColor(19, 28, 37))
    label.set_chart(src)
    QApplication.processEvents()
    for width in (450, 340, 260, 510):
        label.resize(width, 278)
        QApplication.processEvents()
        actual = label.pixmap()
        assert actual is not None and not actual.isNull()
        assert actual.width() <= label.contentsRect().width()
        assert actual.height() <= label.contentsRect().height()
        assert actual.width()/actual.height() == pytest.approx(
            src.width()/src.height(), rel=.012)
    # A scientific rejection before fitting must never retain the
    # previous band's or previous run's rendered pixmap.
    label.set_placeholder("No fitted model; no radial data.")
    assert label.pixmap() is None or label.pixmap().isNull()
    assert "No fitted model" in label.text()
    label.resize(340, 278)
    QApplication.processEvents()
    assert label.pixmap() is None or label.pixmap().isNull()


def test_psf0_empty_cards_are_compact_and_expand_only_for_real_data(qtbot):
    """No-fit must not allocate 240/278 px canvases; real arrays restore them."""
    from lsbpipe.gui.psf0_panel import PSF0Panel

    panel = PSF0Panel(fixed_band="g")
    qtbot.addWidget(panel)
    panel.resize(1200, 700)
    panel.show()
    QApplication.processEvents()
    assert panel.kernel_view.isVisible()
    assert panel.profile_view.isVisible()
    assert panel.kernel_view.height() == 56
    assert panel.profile_view.height() == 56
    assert not panel.model_caption.isVisible()
    assert not panel.profile_caption.isVisible()
    assert panel.kernel_view.pixmap() is None or panel.kernel_view.pixmap().isNull()
    assert panel.profile_view.pixmap() is None or panel.profile_view.pixmap().isNull()

    panel._clear_science_visuals("Star-selection underflow: 11 of 13.")
    assert panel.kernel_view.height() == 56
    assert panel.profile_view.height() == 56
    assert "11 of 13" in panel.model_caption.text()

    y, x = np.mgrid[:31, :31]
    model = np.exp(-((x-15.)**2 + (y-15.)**2) / 25.)
    signed = model - .002
    panel._show_signed_rejected_kernel(signed)
    assert panel.kernel_view.height() == 240
    assert panel.kernel_view.pixmap() is not None
    assert panel.model_caption.isVisible()
    assert "REJECTED" in panel.model_caption.text()
    assert panel.profile_view.height() == 56

    panel._star_review_data = {
        "schema": "desidera.psf0-star-review-in-memory/1",
        "stars": [{
            "star_id": "train-1", "role": "train",
            "cutout": model*1.01, "model": model,
            "valid": np.ones((31,31), bool),
            "center_in_cutout": (15., 15.),
            "peak_nMgy": 1.,
        }],
    }
    panel._show_real_radial_profile()
    assert panel.profile_view.height() == 278
    assert panel.profile_caption.isVisible()
    assert panel.profile_view.pixmap() is not None
    assert "in-sample" in panel.profile_caption.text()

    panel._clear_science_visuals("No fitted model after source change.")
    assert panel.kernel_view.height() == 56
    assert panel.profile_view.height() == 56
    assert not panel.model_caption.isVisible()
    assert not panel.profile_caption.isVisible()
    assert panel.kernel_view.pixmap() is None or panel.kernel_view.pixmap().isNull()
    assert panel.profile_view.pixmap() is None or panel.profile_view.pixmap().isNull()


def test_griz_selector_remains_visible_when_science_workspace_scrolls(qtbot):
    """The selected-band navigation must not be clipped with page content."""
    from PySide6.QtWidgets import QScrollArea
    from lsbpipe.gui.photometry_workspace import PhotometryWorkspace

    workspace = PhotometryWorkspace()
    qtbot.addWidget(workspace)
    workspace.resize(1200, 640)
    workspace.show()
    QApplication.processEvents()
    scroll = next(
        area for area in workspace.findChildren(QScrollArea)
        if area.widget().objectName() == "photometry_narrow_science_column")
    assert workspace.band_selector.isVisible()
    assert not workspace.band_tabs.tabBar().isVisible()
    scroll.verticalScrollBar().setValue(
        scroll.verticalScrollBar().maximum())
    QApplication.processEvents()
    assert workspace.band_selector.isVisible()
    assert workspace.band_selector.mapTo(
        workspace, workspace.band_selector.rect().topLeft()).y() < (
            scroll.mapTo(workspace, scroll.rect().topLeft()).y())
    workspace.band_selector.setCurrentIndex(2)
    assert workspace.band_tabs.currentIndex() == 2
    workspace.band_tabs.setCurrentIndex(3)
    assert workspace.band_selector.currentIndex() == 3
