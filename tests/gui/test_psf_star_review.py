"""Offline Qt science UX: star mosaic and researcher-only train exclusion."""
import numpy as np
import pytest

pytest.importorskip("pytestqt")
pytest.importorskip("PySide6")
from PySide6.QtWidgets import QCheckBox, QLabel, QDialog

from lsbpipe.gui.psf_star_review import PSFStarReviewDialog
from lsbpipe.gui.psf0_panel import PSF0Panel


def _snapshot():
    yy, xx = np.indices((21,21), dtype=float)
    stamps=[]
    for sid, x0 in (("star-0002", 88.), ("star-0014", 140.)):
        data = 42*np.exp(-.5*((xx-10.2)**2+(yy-9.7)**2)/4.0)
        model = 41*np.exp(-.5*((xx-10.2)**2+(yy-9.7)**2)/4.0)
        model[-1, -1] = -.01
        stamps.append({
            "star_id":sid, "role":"train", "x_pix":x0,
            "y_pix":144., "ra_deg":155.4, "dec_deg":18.1,
            "cutout":data, "model":model, "valid":np.ones_like(data,dtype=bool),
            "center_in_cutout":(10.2,9.7),
            "peak_nMgy":42., "sky_nMgy":.05,
            "gradient_sigma_span":.17,
        })
    return {
        "schema":"desidera.psf0-star-review-in-memory/1",
        "stars":stamps,
        "excluded_training_star_ids":(),
        "guessed_fwhm_pix":4.8,
        "status":"RESEARCHER_REVIEW_NOT_SCIENCE_ADOPTED",
    }


def test_star_review_mosaic_has_four_square_graphics_and_manual_restore(qtbot):
    dialog = PSFStarReviewDialog(_snapshot())
    qtbot.addWidget(dialog)
    dialog.show()
    assert dialog.objectName() == "psf0_training_star_mosaic"
    assert len(dialog._controls) == 2
    assert dialog.excluded_ids() == ()
    for sid in ("star-0002", "star-0014"):
        card = dialog.findChild(
            QLabel, "psf0_review_cutout_"+sid)
        model = dialog.findChild(
            QLabel, "psf0_review_psf_model_"+sid)
        residual = dialog.findChild(
            QLabel, "psf0_review_2d_residual_"+sid)
        radial = dialog.findChild(
            QLabel, "psf0_review_radial_psf_"+sid)
        for item in (card, model, residual, radial):
            assert item is not None
            assert not item.pixmap().isNull()
            assert item.pixmap().width() == item.pixmap().height()
        stats = dialog.findChild(
            QLabel, "psf0_residual_stats_"+sid)
        assert stats is not None
        assert "diagnostic only" in stats.text()
    dialog.findChild(QCheckBox, "psf0_exclude_star-0002").setChecked(True)
    assert dialog.excluded_ids() == ("star-0002",)
    assert dialog.changed()
    assert "excluded manually: 1" in dialog.count_label.text()
    dialog.findChild(QCheckBox, "psf0_exclude_star-0014").setChecked(True)
    assert len(dialog.excluded_ids()) == 2
    dialog._restore_all()
    assert dialog.excluded_ids() == ()
    assert dialog.changed() is False


def test_review_btn_only_enabled_after_snapshot_and_emits_apply_rebuild(qtbot,
                                                                       monkeypatch):
    panel = PSF0Panel()
    qtbot.addWidget(panel)
    panel.show()
    assert not panel.review_btn.isEnabled()
    # Simulate an existing valid band/model; no need to write pretend FITS.
    panel._project_root = "C:/test/Leo_P"
    panel._snapshot = {"bands":[{"band":"g","paths_present_unverified":True}]}
    panel.band.addItem("g · verified fixture", "g")
    panel._history_band = "g"
    panel._star_review_data = _snapshot()
    panel._update_buttons()
    assert panel.review_btn.isEnabled()
    calls = []
    panel.runRequested.connect(calls.append)

    def accept_one(self):
        self.findChild(QCheckBox, "psf0_exclude_star-0014").setChecked(True)
        return QDialog.Accepted

    monkeypatch.setattr(PSFStarReviewDialog, "exec", accept_one)
    panel.review_btn.click()
    assert len(calls) == 1
    assert calls[0]["excluded_training_star_ids"] == ("star-0014",)
    assert calls[0]["manual_review_confirmed"] is True
    assert calls[0]["project_root"] == "C:/test/Leo_P"
    assert not panel.review_btn.isEnabled()
    assert panel.active_preview is None


def test_persisted_training_exclusions_are_prechecked_not_automatically_fitted(qtbot):
    snapshot = _snapshot()
    snapshot["excluded_training_star_ids"] = ("star-0002",)
    dialog = PSFStarReviewDialog(snapshot)
    qtbot.addWidget(dialog)
    assert dialog.excluded_ids() == ("star-0002",)
    assert dialog.changed() is False
    assert dialog.findChild(
        QCheckBox, "psf0_exclude_star-0002").isChecked()
    dialog.findChild(QCheckBox, "psf0_exclude_star-0002").setChecked(False)
    assert dialog.excluded_ids() == ()
    assert dialog.changed()
    # When a sufficient sample is not left, the UI must refuse Apply.
    too_strict = _snapshot()
    too_strict["min_train_stars"] = 2
    blocking_dialog = PSFStarReviewDialog(too_strict)
    qtbot.addWidget(blocking_dialog)
    assert blocking_dialog.apply_button.isEnabled()
    blocking_dialog.findChild(
        QCheckBox, "psf0_exclude_star-0014").setChecked(True)
    assert not blocking_dialog.apply_button.isEnabled()


def test_auto_fwhm_suggestion_is_optional_and_protection_radius_unaffected(qtbot):
    panel = PSF0Panel()
    qtbot.addWidget(panel)
    panel._project_root = "C:/test/Leo_P"
    panel.band.addItem("g", "g")
    panel.protection.setValue(90.)
    panel.show_fwhm_suggestion({
        "project_root":"C:/test/Leo_P", "band":"g",
        "seeing":{"available":True,
                  "psfsize_arcsec":1.2,
                  "image_pixel_scale_arcsec":.262,
                  "suggested_detection_fwhm_pix":1.2/.262},
    })
    assert panel.fwhm.value() == pytest.approx(round(1.2/.262,2))
    assert panel.protection.value() == pytest.approx(90.)
    assert "PSFSIZE" in panel.prior_source.text()
    last = panel.fwhm.value()
    panel.show_fwhm_suggestion({
        "project_root":"C:/test/Leo_P", "band":"g",
        "seeing":{"available":False,
                  "message":"Optional local PSFSIZE map absent"},
    })
    assert panel.fwhm.value() == last
    assert panel.protection.value() == 90.
