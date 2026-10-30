import numpy as np
import pytest
from lsbpipe import ReferenceGeometry, SersicTruth, sersic_image, analytic_sersic_flux, MeasurementFrame, measure_profiles


def test_analytic_half_light_by_definition():
    truth = SersicTruth(ReferenceGeometry(90,90,.6,19), 17, 1.2,.04)
    assert analytic_sersic_flux(truth, truth.re_pix) == pytest.approx(truth.total_nMgy * .5, rel=1e-13)


@pytest.mark.parametrize('q,pa,n',[(1,0,1),(.7,40,1),(.5,-26,2)])
def test_sersic_numerical_growth_matches_analytic(q,pa,n):
    geom = ReferenceGeometry(110.1,110.3,q,pa)
    truth = SersicTruth(geom, 18, n, .07)
    image = sersic_image((224,224),truth,subpixels=8)
    out = measure_profiles(MeasurementFrame(image,.262),geom,[0,10,20,32,50,70],subpixels=12)
    target = np.array([analytic_sersic_flux(truth,r) for r in [10,20,32,50,70]])
    assert np.allclose(out.cog_raw_nMgy,target,rtol=.012)


def test_sersic_image_is_deterministic():
    truth = SersicTruth(ReferenceGeometry(33,34,.8,22),14,1,.1)
    a=sersic_image((80,80),truth)
    b=sersic_image((80,80),truth)
    assert np.array_equal(a,b)
