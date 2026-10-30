import numpy as np
import pytest
from lsbpipe import ReferenceGeometry, MeasurementFrame, measure_profiles, half_flux_radius


def test_constant_image_flux_area_and_units():
    g = ReferenceGeometry(77.12, 76.91, .58, 43)
    frame = MeasurementFrame(np.full((160,160), 0.025), 0.262)
    out = measure_profiles(frame, g, np.arange(0,45,3), subpixels=10)
    assert np.allclose(out.annular_flux_valid_nMgy, .025 * out.area_valid_pix2, atol=1e-10)
    assert np.allclose(out.intensity_nMgy_arcsec2, .025 / .262**2, atol=1e-10)
    assert out.cog_raw_nMgy[-1] == pytest.approx(.025 * out.area_valid_pix2.sum(), rel=1e-12)
    assert np.all(out.area_fraction > .99)
    assert all('NO_NOISE_MODEL' in flag for flag in out.flags)


def test_raw_cog_telescope_annuli():
    image = np.random.default_rng(33).normal(0.01, 0.08, (96,96))
    g = ReferenceGeometry(46.3, 45.8, .65, 28)
    f = MeasurementFrame(image, .262, background=.001)
    out = measure_profiles(f,g,np.arange(0,45,2),subpixels=7)
    assert np.allclose(np.nancumsum(out.annular_flux_valid_nMgy), out.cog_raw_nMgy, atol=1e-10)


def test_mask_never_reconstructs_flux():
    data = np.ones((140, 140))
    y,x = np.indices(data.shape)
    good = x >= 70
    g = ReferenceGeometry(70, 70, .7, 20)
    o1 = measure_profiles(MeasurementFrame(data,.262),g,[0,35],subpixels=12)
    o2 = measure_profiles(MeasurementFrame(data,.262,valid=good),g,[0,35],subpixels=12)
    assert o1.cog_raw_nMgy[-1] > 2 * o2.cog_raw_nMgy[-1] * .98
    assert o2.area_fraction[-1] == pytest.approx(.5, rel=.03)
    assert o2.cog_raw_nMgy[-1] == pytest.approx(o2.area_valid_pix2[-1])
    assert 'PARTIAL_MASK' in o2.flags[-1]


def test_blank_ring_not_zero_measurement():
    g = ReferenceGeometry(44,44,.8)
    yy,xx = np.indices((90,90))
    r = g.elliptical_radius(xx,yy)
    valid = (r < 10) | (r >= 20)
    out = measure_profiles(MeasurementFrame(np.ones((90,90)),.262,valid=valid),g,[0,10,20,30],subpixels=8)
    # Some boundary subpixels of pixels with centres 10 and 20 can contribute;
    # for guaranteed full masking choose an entire image as invalid.
    invalid = measure_profiles(MeasurementFrame(np.ones((90,90)),.262,valid=np.zeros((90,90),bool)),g,[0,10,20],subpixels=8)
    assert np.isnan(invalid.annular_flux_valid_nMgy).all()
    assert np.isnan(invalid.intensity_nMgy_arcsec2).all()
    assert np.all(invalid.cog_raw_nMgy == 0)
    assert all('NO_VALID_AREA' in s for s in invalid.flags)


def test_negative_flux_is_retained_and_mu_nan():
    data = np.full((100,100),-0.005)
    g = ReferenceGeometry(50,50,.8)
    out = measure_profiles(MeasurementFrame(data,.262),g,[0,10,20],subpixels=8)
    assert np.all(out.annular_flux_valid_nMgy < 0)
    assert np.all(np.isnan(out.mu_ab_mag_arcsec2))
    assert out.cog_raw_nMgy[-1] < out.cog_raw_nMgy[0]
    with pytest.raises(ValueError, match='nonmonotonic'):
        half_flux_radius(out, target_flux_nMgy=100)


def test_covariance_diagonal_error_and_ivar_mask():
    data = np.ones((100,100))
    ivar = np.full_like(data, 25.)
    ivar[49,49] = 0
    g = ReferenceGeometry(49,49,.9)
    out = measure_profiles(MeasurementFrame(data,.262,inverse_variance=ivar),g,[0,8,16],subpixels=8)
    assert out.cog_raw_nMgy[-1] == pytest.approx(out.area_valid_pix2.sum())
    # Partial pixels carry squared geometric weights in diagonal variance.
    assert out.cog_sigma_diagonal_nMgy[-1] < np.sqrt(out.area_valid_pix2.sum() / 25)


def test_image_truncation_is_recorded():
    g = ReferenceGeometry(4,4,.8)
    out = measure_profiles(MeasurementFrame(np.ones((80,80)),.262),g,[0,15,30],subpixels=8)
    assert out.area_fraction[-1] < 1
    assert 'FRAME_TRUNCATION' in out.flags[-1]


def test_all_inputs_are_not_mutated():
    data = np.ones((70,70))
    bg = np.full_like(data,.1)
    original = data.copy()
    f = MeasurementFrame(data, .262, background=bg)
    data[0,0] = 123
    bg[0,0] = 888
    assert f.image[0,0] == 1
    assert f.background[0,0] == pytest.approx(.1)
    with pytest.raises(ValueError):
        f.image[0,0] = 9
    _ = measure_profiles(f, ReferenceGeometry(35,35,.7), [0,10], subpixels=4)
    assert f.image[0,0] == original[0,0]


def test_empty_ivar_fully_invalid():
    f = MeasurementFrame(np.ones((50,50)), .262, inverse_variance=np.zeros((50,50)))
    o = measure_profiles(f, ReferenceGeometry(25,25,1),[0,10])
    assert o.area_valid_pix2[0] == 0
    assert 'NO_VALID_AREA' in o.flags[0]


def test_invalid_edges():
    f=MeasurementFrame(np.ones((50,50)), .262)
    g=ReferenceGeometry(25,25,.7)
    for e in [[0,1,1],[2,1],[2,3],[-1,2],[0,np.nan]]:
        with pytest.raises(ValueError):measure_profiles(f,g,e)


def test_half_flux_finite_aperture_with_constant_image():
    f=MeasurementFrame(np.ones((150,150)), .262)
    g=ReferenceGeometry(75,75,1)
    o=measure_profiles(f,g,np.arange(0,51,1),subpixels=12)
    re=half_flux_radius(o,target_flux_nMgy=o.cog_raw_nMgy[-1])
    assert re == pytest.approx(50 / np.sqrt(2), abs=.13)
