import numpy as np
import pytest
from lsbpipe.sky_geometry import ellipse_from_jacobian


@pytest.mark.parametrize('k',[
    [[-1/.262,0],[0,1/.262]],    # north-up with RA reflection
    [[1/.262,0],[0,1/.262]],      # east-right
    [[0,-1/.262],[1/.262,0]],     # rotated by 90 deg
    [[-3.2,.4],[.2,2.5]],        # shear + anisotropy + reflection
    [[1.8,1.2],[-.7,3.3]],       # arbitrary affine distortion
])
@pytest.mark.parametrize('pa,q',[(0.,.3),(37.,.72),(92.,.95),(179.,.5)])
def test_sky_ellipse_boundary_maps_to_pixel_exactly_under_affine(k,pa,q):
    k=np.array(k,dtype=float)
    geo=ellipse_from_jacobian(21.5,17.3,q,pa,k)
    phi=np.linspace(0,2*np.pi,121)
    pa_rad=np.deg2rad(pa)
    maj=np.array([np.sin(pa_rad),np.cos(pa_rad)])
    minor=np.array([np.cos(pa_rad),-np.sin(pa_rad)])
    offsets_en=maj[:,None]*np.cos(phi) + q*minor[:,None]*np.sin(phi)
    xy=k@offsets_en
    radius=geo.pixel.elliptical_radius(xy[0]+21.5,xy[1]+17.3)
    assert np.allclose(radius,geo.pixel_a_per_sky_arcsec,atol=2e-12)
    assert geo.pixel_area_arcsec2==pytest.approx(1./abs(np.linalg.det(k)), rel=1e-12)


def test_pa_mod_180_and_invalid_jacobians():
    k=np.array([[-3.,0],[0,3.]])
    a=ellipse_from_jacobian(10,20,.5,25.,k)
    b=ellipse_from_jacobian(10,20,.5,205.,k)
    assert a.pixel.q==pytest.approx(b.pixel.q)
    assert a.pixel.theta_image_deg==pytest.approx(b.pixel.theta_image_deg)
    for wrong in [np.zeros((2,2)),np.array([[1,0],[2,0]]),np.ones((3,3))]:
        with pytest.raises(ValueError):ellipse_from_jacobian(0,0,.7,0,wrong)


def test_asymmetric_sky_radii():
    e=ellipse_from_jacobian(3,4,.3,40,np.array([[2.,.5],[0.,4.]]))
    assert np.allclose(e.edges_pix([0,2,5]),np.array([0,2,5])*e.pixel_a_per_sky_arcsec)
    with pytest.raises(ValueError):e.edges_pix([-1,2])
