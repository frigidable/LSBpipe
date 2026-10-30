"""End-to-end FITS tests run only when external Astropy+Photutils are installed."""
import subprocess
import sys
from pathlib import Path
import pytest
import numpy as np

astropy = pytest.importorskip('astropy')
from astropy.io import fits
from astropy.wcs import WCS
from lsbpipe.survey import load_legacy_coadd
from lsbpipe.fits_workflow import measure_fits_observation, export_fits_measurement
from lsbpipe.sky_geometry import ellipse_from_wcs


def _fixture(tmp_path):
    from importlib.util import spec_from_file_location, module_from_spec
    path=Path(__file__).parent.parent/'scripts/make_mock_legacy_fits.py'
    spec=spec_from_file_location('mockfits',path)
    mod=module_from_spec(spec);spec.loader.exec_module(mod)
    return mod.generate(tmp_path)


def test_fits_ingestion_and_wcs(tmp_path):
    folder=_fixture(tmp_path)
    o=load_legacy_coadd(folder/'mock-image-r.fits',folder/'mock-invvar-r.fits',
                        folder/'mock-maskbits.fits',band='r')
    assert o.shape==(128,128)
    assert o.provenance['sha256']['image']
    assert o.pixel_area_arcsec2==pytest.approx(.262**2,rel=1e-5)
    assert o.mask_reasons['GALAXY'][30,30] and o.valid[30,30]
    assert not o.valid[55,61] and not o.valid[56,61]
    sky=o.sky_ellipse(96.0619,77.9684,q=.63,pa_deg=127)
    assert sky.pixel.q==pytest.approx(.63,abs=2e-3)
    assert sky.pixel_area_arcsec2==pytest.approx(.262**2,rel=1e-5)
    assert np.hypot(sky.pixel.x_pix-63.5,sky.pixel.y_pix-63.5)<1e-2
    m=measure_fits_observation(o,ra_deg=96.0619,dec_deg=77.9684,
       q=.63,pa_deg=127,radius_max_arcsec=7.,step_arcsec=1.,backend='subpixel',subpixels=8,
       background_nMgy_per_pixel=.0003)
    assert np.isfinite(m.result.cog_raw_nMgy[-1])
    manifest=export_fits_measurement(m,tmp_path/'out')
    assert manifest['scientific_status']=='EXPERIMENTAL_NOT_ADOPTED'
    assert (tmp_path/'out/radial_profile.csv').is_file()


def test_mismatched_wcs_same_shape_is_rejected(tmp_path):
    folder=_fixture(tmp_path)
    p=folder/'mock-invvar-r.fits'
    with fits.open(p,mode='update') as f:
        f[0].header['CRVAL1']=96.0629
        f.flush()
    with pytest.raises(ValueError,match='WCS grid mismatch'):
        load_legacy_coadd(folder/'mock-image-r.fits',p,folder/'mock-maskbits.fits',band='r')


def test_exact_backend_photutils_if_available(tmp_path):
    pytest.importorskip('photutils')
    folder=_fixture(tmp_path)
    o=load_legacy_coadd(folder/'mock-image-r.fits',folder/'mock-invvar-r.fits',folder/'mock-maskbits.fits',band='r')
    m=measure_fits_observation(o,ra_deg=96.0619,dec_deg=77.9684,q=.63,pa_deg=127,
                radius_max_arcsec=6,step_arcsec=1,backend='exact')
    assert m.result.cog_raw_nMgy[-1] > 0


def test_cutout_skips_large_image_and_preserves_grid(tmp_path):
    folder=_fixture(tmp_path)
    o=load_legacy_coadd(folder/'mock-image-r.fits',folder/'mock-invvar-r.fits',
          folder/'mock-maskbits.fits',band='r',cutout_center=(96.0619,77.9684),cutout_size_pix=64)
    assert o.shape==(64,64)
    c=o.wcs.pixel_to_world(31.5,31.5)
    assert c.separation(astropy.coordinates.SkyCoord(96.0619,77.9684,unit='deg')).arcsec<.3
    assert o.provenance['cutout_size_pix']==64
