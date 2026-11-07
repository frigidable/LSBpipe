"""Scientific safeguards for common sky-coordinate mask edits."""
import numpy as np
import pytest

from lsbpipe.photometry.sky_masks import (
    SkyMaskDraft, SkyMaskEdit, SkyMaskShape,
    evaluate_mask_draft, rasterize_sky_shape)


def _wcs(ra=359.9995, *, mirrored=False):
    from astropy.wcs import WCS
    wcs = WCS(naxis=2)
    wcs.wcs.crpix = [26., 26.]
    wcs.wcs.crval = [ra, 0.1]
    wcs.wcs.cd = np.array([[1 if mirrored else -1, 0], [0, 1]])/3600.
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    return wcs


def _draft():
    return SkyMaskDraft("test-galaxy")


def test_shared_sky_circle_moves_to_corresponding_pixels_on_mirrored_wcs():
    from astropy.coordinates import SkyCoord
    from astropy import units as u
    w1, w2 = _wcs(), _wcs(mirrored=True)
    c = SkyCoord(359.9995*u.deg, .1*u.deg)
    source = c.directional_offset_by(90*u.deg, 8*u.arcsec)
    shape = SkyMaskShape("circle", (float(source.ra.deg), float(source.dec.deg)),
                         radius_arcsec=2.)
    assert source.ra.deg < 1 or source.ra.deg > 359  # crosses RA=0
    a = rasterize_sky_shape(shape, w1, (51, 51))
    b = rasterize_sky_shape(shape, w2, (51, 51))
    assert a.sum() > 5 and b.sum() > 5
    y1, x1 = np.unravel_index(a.argmax(), a.shape)
    y2, x2 = np.unravel_index(b.argmax(), b.shape)
    # RA increases toward negative FITS x for the first (mirrored sky) WCS.
    assert x1 < 25 and x2 > 25
    assert abs(y1-y2) <= 1
    assert not np.array_equal(a, b)


def test_polygon_crosses_ra_zero_and_supports_fractional_edge_pixels():
    from astropy.coordinates import SkyCoord
    from astropy import units as u
    c = SkyCoord(359.9995*u.deg, .1*u.deg)
    angles = (45, 135, 225, 315)
    vertices = []
    for angle in angles:
        p = c.directional_offset_by(angle*u.deg, 4*u.arcsec)
        vertices.append((float(p.ra.deg), float(p.dec.deg)))
    shape = SkyMaskShape("polygon", vertices=tuple(vertices))
    values = rasterize_sky_shape(shape, _wcs(), (51, 51), subpixels=3)
    assert values[25, 25] == pytest.approx(1.)
    assert 0 < values.sum() < 100
    assert np.any((values > 0) & (values < 1.))


def test_restore_only_affects_automatic_contamination_not_native_hard():
    wcs = _wcs(ra=155.)
    region = SkyMaskShape("circle", (155., .1), radius_arcsec=3.2)
    draft = _draft().with_edit(SkyMaskEdit("restore-star", "restore_auto", region))
    hard = np.zeros((51, 51), bool)
    hard[25, 25] = True
    hard[25, 28] = True
    auto = np.zeros_like(hard)
    auto[22:29, 22:29] = True
    out = evaluate_mask_draft(
        draft, wcs, hard.shape, "r", native_hard=hard,
        automatic_contaminants=auto)
    assert out["native_hard"][25, 25]
    assert out["excluded"][25, 25]
    assert not out["usable"][25, 28]
    assert out["usable"][25, 24]  # auto can be restored by explicit choice
    assert out["status"] == "MASK_DRAFT_NOT_ADOPTED"


def test_manual_exclusion_has_priority_over_restore_and_band_scopes():
    wcs = _wcs(ra=155.)
    shape = SkyMaskShape("circle", (155., .1), radius_arcsec=3.)
    draft = _draft().with_edit(
        SkyMaskEdit("restore", "restore_auto", shape)).with_edit(
        SkyMaskEdit("exclude-g", "exclude", shape, band="g"))
    hard = np.zeros((51, 51), bool)
    auto = np.ones_like(hard)
    g = evaluate_mask_draft(draft, wcs, hard.shape, "g",
                            native_hard=hard, automatic_contaminants=auto)
    r = evaluate_mask_draft(draft, wcs, hard.shape, "r",
                            native_hard=hard, automatic_contaminants=auto)
    assert g["excluded"][25, 25] and not r["excluded"][25, 25]
    assert not g["usable"][0, 0] and not r["usable"][0, 0]
    assert len(draft.edits) == 2 and len(draft.without_edit("exclude-g").edits) == 1


def test_masks_reject_bad_shapes_and_disallow_training_holdout_overlap():
    with pytest.raises(ValueError):
        SkyMaskShape("ellipse", (155., .1), a_arcsec=20., q=0.)
    with pytest.raises(ValueError):
        SkyMaskShape("circle", (-1., .1), radius_arcsec=2.)
    with pytest.raises(ValueError):
        SkyMaskShape("polygon", vertices=((1., 1.), (2., 2.)))
    shape = SkyMaskShape("circle", (155., .1), radius_arcsec=3.)
    draft = _draft().with_edit(
        SkyMaskEdit("train", "sky_train", shape)).with_edit(
        SkyMaskEdit("holdout", "sky_holdout", shape))
    with pytest.raises(ValueError, match="must not overlap"):
        evaluate_mask_draft(draft, _wcs(ra=155.), (51, 51), "g",
                            native_hard=np.zeros((51, 51), bool))


def test_edit_serialization_immutable_revision_and_digest():
    shape = SkyMaskShape("ellipse", (155., .1), a_arcsec=15., q=.55, pa_deg=210.)
    initial = _draft()
    later = initial.with_edit(
        SkyMaskEdit("one", "exclude", shape, comment="foreground star"))
    reconstructed = SkyMaskDraft.from_dict(later.to_dict())
    assert shape.pa_deg == pytest.approx(30.)
    assert reconstructed == later
    assert reconstructed.digest == later.digest
    assert len(initial.edits) == 0 and later.revision == 1
    with pytest.raises(ValueError):
        later.with_edit(later.edits[0])
    with pytest.raises(ValueError):
        SkyMaskDraft.from_dict({**later.to_dict(), "status": "SCIENCE_READY"})


def test_icrs_ellipse_area_mirror_symmetry_and_chunk_invariance():
    """A PSF/sky mask's physical footprint cannot depend on the FITS x handedness."""
    shape = SkyMaskShape(
        "ellipse", (359.9995, .1), a_arcsec=9., q=.45, pa_deg=35.)
    first = rasterize_sky_shape(
        shape, _wcs(), (51, 51), subpixels=4, row_chunk=7)
    same_different_chunks = rasterize_sky_shape(
        shape, _wcs(), (51, 51), subpixels=4, row_chunk=51)
    mirrored = rasterize_sky_shape(
        shape, _wcs(mirrored=True), (51, 51), subpixels=4, row_chunk=11)
    expected_sky_area_arcsec2 = np.pi * 9.**2 * .45
    assert np.array_equal(first, same_different_chunks)
    assert np.all((first >= 0.) & (first <= 1.))
    assert np.any((first > 0.) & (first < 1.))
    # The WCS fixtures have 1-arcsec pixels. An ellipse's sky area and
    # fractional occupancy must agree regardless of mirrored x orientation.
    assert first.sum() == pytest.approx(expected_sky_area_arcsec2, rel=.03)
    assert mirrored.sum() == pytest.approx(expected_sky_area_arcsec2, rel=.03)
    assert first.sum() == pytest.approx(mirrored.sum(), rel=.02)
    assert first[25, 25] == pytest.approx(1.)
    assert mirrored[25, 25] == pytest.approx(1.)
