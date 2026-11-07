"""Offline Qt: Gaia identity is an explicit mode and never a silent fallback."""
import pytest
pytest.importorskip("pytestqt")
pytest.importorskip("PySide6")
from lsbpipe.gui.psf0_panel import PSF0Panel
import lsbpipe.gui.psf0_panel as panel_module


def _inventory(*, available):
    return {
        "project_id":"00000000-0000-4000-8000-000000000001",
        "project_root":"C:/test/Leo_P",
        "dataset":("dr11","south","1554p180","g"),
        "bands":[{"band":"g","selected":True,"present_count":4,
                  "paths_present_unverified":True,
                  "files":{"image":True,"invvar":True,
                           "nexp":True,"maskbits":True}}],
        "gaia_tractor_path":(
            "C:/test/data/dr11/south/tractor/155/tractor-1554p180.fits"),
        "gaia_tractor_present_unverified":available,
        "target_protection_default_arcsec":90.,
    }


def test_missing_native_gaia_requires_user_action_and_never_silent_fallback(
        qtbot, monkeypatch):
    monkeypatch.setattr(panel_module, "inspect_psf0_bands",
                        lambda root: _inventory(available=False))
    panel=PSF0Panel()
    qtbot.addWidget(panel)
    panel.bind_project("C:/test/Leo_P")
    assert panel.catalog_policy.currentData()=="dao_diagnostic"
    assert panel.run_btn.isEnabled()
    requests=[]
    panel.tractorRequested.connect(requests.append)
    panel.get_tractor_btn.click()
    assert requests==[{"project_root":"C:/test/Leo_P","band":"g"}]
    panel.catalog_policy.setCurrentIndex(
        panel.catalog_policy.findData("gaia_strict"))
    assert panel.catalog_policy.currentData()=="gaia_strict"
    assert not panel.run_btn.isEnabled()
    assert not panel.active_preview
    assert not panel.review_btn.isEnabled()


def test_existing_native_tractor_defaults_to_gaia_strict_but_is_not_adopted(
        qtbot,monkeypatch):
    monkeypatch.setattr(panel_module,"inspect_psf0_bands",
                        lambda root:_inventory(available=True))
    panel=PSF0Panel()
    qtbot.addWidget(panel)
    panel.bind_project("C:/test/Leo_P")
    assert panel.catalog_policy.currentData()=="gaia_strict"
    assert panel.run_btn.isEnabled()
    assert not panel.save_btn.isEnabled()
    assert "not yet validated" in panel.gaia_source.text()
    calls=[]
    panel.runRequested.connect(calls.append)
    panel.run_btn.click()
    assert calls[0]["star_catalog_policy"]=="gaia_strict"
    assert calls[0]["excluded_training_star_ids"]==()
    assert calls[0]["manual_review_confirmed"] is False
    assert panel.active_preview is None
