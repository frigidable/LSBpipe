import numpy as np
import pytest
from lsbpipe import ReferenceGeometry, fractional_aperture_weights


def test_ellipse_axes_rotation():
    g = ReferenceGeometry(30, 30, .5, 30)
    t = np.deg2rad(30)
    r_major = g.elliptical_radius(np.array([30+10*np.cos(t)]), np.array([30+10*np.sin(t)]))[0]
    r_minor = g.elliptical_radius(np.array([30-5*np.sin(t)]), np.array([30+5*np.cos(t)]))[0]
    assert r_major == pytest.approx(10)
    assert r_minor == pytest.approx(10)


def test_geometry_validation():
    with pytest.raises(ValueError): ReferenceGeometry(0, 0, 0)
    with pytest.raises(ValueError): ReferenceGeometry(0, 0, 1.01)
    with pytest.raises(ValueError): ReferenceGeometry(np.nan, 0, .7)


@pytest.mark.parametrize('q,angle', [(1., 0.), (.64, 0.), (.67, 33.), (.31, 82.)])
def test_fractional_area_matches_analytic(q, angle):
    g = ReferenceGeometry(101.12, 101.47, q, angle)
    weights = fractional_aperture_weights((210,210), g, 42, subpixels=12)
    assert weights.min() >= 0 and weights.max() <= 1
    assert np.sum(weights) == pytest.approx(g.area_pix2(42), rel=0.002)


def test_weights_nested_and_centre():
    g = ReferenceGeometry(41.8, 42.3, .38, 67)
    prev = fractional_aperture_weights((85,85), g, 5, subpixels=8)
    for radius in [6.5, 9, 14, 26]:
        curr = fractional_aperture_weights((85,85), g, radius, subpixels=8)
        assert np.all(curr + 1e-12 >= prev)
        prev = curr


def test_image_boundaries_leave_missing_area():
    g = ReferenceGeometry(2, 2, .8, 15)
    w = fractional_aperture_weights((30,30), g, 14, subpixels=8)
    assert np.sum(w) < g.area_pix2(14) * .8


def test_exact_optional_graceful_failure():
    g = ReferenceGeometry(20, 20, .8)
    try:
        import photutils
    except ImportError:
        with pytest.raises(RuntimeError, match='Photutils'):
            fractional_aperture_weights((50, 50), g, 10, backend='exact')
    else:
        w = fractional_aperture_weights((50, 50), g, 10, backend='exact')
        assert w.sum() == pytest.approx(g.area_pix2(10), rel=1e-9)
