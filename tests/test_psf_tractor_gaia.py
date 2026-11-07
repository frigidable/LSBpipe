"""DR11 Tractor/Gaia DR3 evidence must be native and conservative."""
import numpy as np
import pytest
pytest.importorskip("astropy")
from astropy.io import fits
from astropy.wcs import WCS
from astropy.table import Table

from lsbpipe.photometry.contracts import NativeBandFrame
from lsbpipe.photometry.psf_tractor_gaia import (
    TractorGaiaMatcher, read_native_tractor_gaia, tractor_catalog_location,
    FIT_REJECT_BITS, MASK_REJECT_BITS,
)
from lsbpipe.photometry.psf_star_quality import (
    assess_psf_star_cutout, assess_sample_widths,
)


def _row(**updates):
    r = {
        "ref_cat":"G3","ref_id":1029384756111,"objid":17,
        "type":"PSF","fitbits":0,"maskbits":0,
        "gaia_duplicated_source":False,"flux":2.,
        "flux_ivar":200.,"gaia_g_mag":18.6,
    }
    r.update(updates)
    return r


def test_strict_gaia_requires_unambiguous_reference_and_real_tractor_psf():
    rows = (
        _row(),_row(ref_cat="",ref_id=0),_row(type="EXP"),
        _row(fitbits=(1<<0)),_row(maskbits=(1<<1)),
        _row(gaia_duplicated_source=True),_row(flux_ivar=0.),
        _row(maskbits=(1<<2))
    )
    points = np.array([[20.+20.*i,20.] for i in range(len(rows))])
    m=TractorGaiaMatcher(
        "synthetic.fits","a"*64,"dr11/south/1554p180/g",rows,points)
    assert m.match(20.4,20.1,band="g")[0]["gaia_source_id"] == rows[0]["ref_id"]
    assert m.match(20.4,20.1,band="g")[0]["match_offset_pix"] < 1
    assert m.match(40,20,band="g")[1] == "NOT_GAIA_DR3"
    assert m.match(60,20,band="g")[1] == "TRACTOR_NONSTELLAR_MODEL"
    assert m.match(80,20,band="g")[1] == "TRACTOR_FORCED_OR_FROZEN_PSF"
    assert m.match(100,20,band="g")[1] == "TRACTOR_BRIGHT_OR_UNSAFE_MASK"
    assert m.match(120,20,band="g")[1] == "GAIA_DUPLICATED_SOURCE"
    assert m.match(140,20,band="g")[1] == "TRACTOR_NO_FITTED_BAND_FLUX"
    assert m.match(160,20,band="g")[1] == "TRACTOR_SATURATED_OR_ALLMASK"
    assert m.match(700,20,band="g")[1] == "NO_TRACTOR_SOURCE_AT_DAO_CENTROID"
    near=TractorGaiaMatcher(
        "synthetic.fits","a"*64,"dr11/south/1554p180/g",
        (_row(),_row(ref_id=2)),
        np.array([[1.,1.],[2.,1.]]))
    assert near.match(1.5,1.,band="g")[1] == "AMBIGUOUS_TRACTOR_CROSSMATCH"


def _wcs(shape=(61,61)):
    wcs=WCS(naxis=2)
    wcs.wcs.crpix=[shape[1]/2+.5,shape[0]/2+.5]
    wcs.wcs.crval=[155.438012,18.088025]
    wcs.wcs.cdelt=[-.262/3600.,.262/3600.]
    wcs.wcs.ctype=["RA---TAN","DEC--TAN"]
    return wcs


def _frame(root):
    shape=(61,61)
    return NativeBandFrame(
        image_nMgy=np.ones(shape),
        invvar_nMgy2=np.ones(shape),
        nexp=np.ones(shape,dtype=np.int16),
        maskbits=np.zeros(shape,dtype=np.int16),
        native_wcs=_wcs(shape),
        release="dr11",region="south",brick="1554p180",
        band="g",source_evidence={"synthetic":True})


def test_tractor_reader_uses_exact_native_brick_and_wcs(tmp_path):
    root=tmp_path/"original"
    path,url=tractor_catalog_location(
        root,release="dr11",region="south",brick="1554p180")
    assert "dr11/south/tractor/155/tractor-1554p180.fits" in url
    path.parent.mkdir(parents=True)
    wcs=_wcs()
    positions=[[19,21],[29,30],[40,25]]
    ra,dec=wcs.all_pix2world(
        [x for x,y in positions],[y for x,y in positions],0)
    catalog=Table()
    catalog["BRICKNAME"]=["1554p180"]*3
    catalog["BRICK_PRIMARY"]=[True]*3
    catalog["RA"]=ra
    catalog["DEC"]=dec
    catalog["REF_CAT"]=["G3","G3",""]
    catalog["REF_ID"]=np.array([1234567890123,2234567890123,0],dtype=np.int64)
    catalog["TYPE"]=["PSF","PSF","EXP"]
    catalog["OBJID"]=[1,2,3]
    catalog["FITBITS"]=np.zeros(3,dtype=np.int16)
    catalog["MASKBITS"]=np.zeros(3,dtype=np.int32)
    catalog["FLUX_G"]=np.array([5.,6.,2.],dtype=np.float32)
    catalog["FLUX_IVAR_G"]=np.array([100.,200.,2.],dtype=np.float32)
    catalog["GAIA_DUPLICATED_SOURCE"]=[False,False,False]
    catalog["GAIA_PHOT_G_MEAN_MAG"]=np.array([18.,19.,0.],dtype=np.float32)
    # Original DR11 angular shape metadata (arcsec), not a guessed PSF.
    catalog["SHAPE_R"]=np.array([0.,0.,1.7],dtype=np.float32)
    catalog["SHAPE_E1"]=np.array([0.,0.,.2],dtype=np.float32)
    catalog["SHAPE_E2"]=np.array([0.,0.,-.1],dtype=np.float32)
    catalog["SERSIC"]=np.array([0.,0.,1.],dtype=np.float32)
    catalog["SHAPE_R_IVAR"]=np.array([0.,0.,25.],dtype=np.float32)
    catalog["SHAPE_E1_IVAR"]=np.array([0.,0.,100.],dtype=np.float32)
    catalog["SHAPE_E2_IVAR"]=np.array([0.,0.,225.],dtype=np.float32)
    catalog.write(path,format="fits")
    frame=_frame(root)
    data=read_native_tractor_gaia(
        root,release="dr11",region="south",brick="1554p180",
        frame=frame)
    assert len(data.rows)==3
    assert data.rows[2]["type"]=="EXP"
    assert data.rows[2]["shape_r_arcsec"]==pytest.approx(1.7,rel=1e-6)
    assert data.rows[2]["shape_e1"]==pytest.approx(.2,rel=1e-6)
    assert data.rows[2]["shape_e2"]==pytest.approx(-.1,rel=1e-6)
    assert data.rows[2]["sersic_n"]==pytest.approx(1.)
    assert data.rows[2]["shape_r_ivar_arcsec_m2"]==pytest.approx(25.)
    assert data.rows[2]["shape_e1_ivar"]==pytest.approx(100.)
    assert data.rows[2]["shape_e2_ivar"]==pytest.approx(225.)
    assert data.rows[2]["morphology_fields_in_native_catalog"]["SHAPE_R_IVAR"]
    assert data.rows[2]["morphology_fields_in_native_catalog"]["SHAPE_R"]
    assert data.source_sha256
    assert data.match(19.1,21.1,band="g")[0]["gaia_source_id"] == 1234567890123
    assert data.match(40.1,25.1,band="g")[1] == "NOT_GAIA_DR3"
    # Original legacy tables lacking optional morphology must leave it
    # explicitly UNKNOWN. They are valid for Gaia identity but cannot
    # silently acquire extended galaxy templates.
    legacy=catalog.copy()
    legacy.remove_columns([
        "SHAPE_R","SHAPE_E1","SHAPE_E2","SERSIC",
        "SHAPE_R_IVAR","SHAPE_E1_IVAR","SHAPE_E2_IVAR"])
    legacy.write(path,format="fits",overwrite=True)
    legacy_data=read_native_tractor_gaia(
        root,release="dr11",region="south",brick="1554p180",
        frame=frame)
    assert legacy_data.rows[2]["shape_r_arcsec"] is None
    assert legacy_data.rows[2]["shape_e1"] is None
    assert legacy_data.rows[2]["shape_e2"] is None
    assert legacy_data.rows[2]["sersic_n"] is None
    assert legacy_data.rows[2]["shape_r_ivar_arcsec_m2"] is None
    assert legacy_data.rows[2]["shape_e1_ivar"] is None
    assert legacy_data.rows[2]["shape_e2_ivar"] is None
    assert not legacy_data.rows[2][
        "morphology_fields_in_native_catalog"]["SHAPE_R"]
    # A modified header/row declaring the wrong brick must fail closed.
    catalog["BRICKNAME"][1]="1999p190"
    catalog.write(path,format="fits",overwrite=True)
    with pytest.raises(ValueError,match="brick identity"):
        read_native_tractor_gaia(
            root,release="dr11",region="south",brick="1554p180",
            frame=frame)


def test_star_cutout_image_quality_screens_nonstellar_elongation():
    yy,xx=np.indices((31,31),dtype=float)
    clean=100*np.exp(-.5*((xx-15)**2+(yy-15)**2)/2.**2)
    long=100*np.exp(-.5*(((xx-15)/5.)**2+((yy-15)/1.5)**2))
    def cutout(image):
        return {
            "image":image,
            "valid":np.ones(image.shape,dtype=bool),
            "center_in_cutout":(15.,15.),
        }
    good=assess_psf_star_cutout(cutout(clean),fwhm_guess_pix=4.8)
    bad=assess_psf_star_cutout(cutout(long),fwhm_guess_pix=4.8)
    assert good["accepted"] and good["axis_ratio"] < 1.1
    assert not bad["accepted"]
    assert bad["reason"]=="ELONGATED_CUTOUT_NOT_ISOLATED_PSF"
    width=assess_sample_widths([("a",good)]*6)
    assert width["reference_core_sigma_pix"] > 0
    assert width["calibration"] == "HEURISTIC_UNVALIDATED"


def test_catalog_matches_reject_low_snr_and_faint_undetected_neighbors():
    p=np.array([[50.,50.],[69.,50.],[130.,90.]])
    rows=(
        _row(ref_id=100,flux=4.,flux_ivar=100.),
        _row(ref_id=101,flux=.5,flux_ivar=400.),
        _row(ref_id=102,flux=1.,flux_ivar=100.),
    )
    matcher=TractorGaiaMatcher(
        "synthetic","d"*64,"dr11/south/1554p180/g",rows,p)
    # Same object is clean at a small neighbor radius, contaminated
    # at a radius covering the PSF/star sky annulus.
    ok,reason=matcher.match(
        50.,50.,band="g",neighbor_search_radius_pix=10.)
    assert reason is None and ok["tractor_band_snr_diagonal_only"]==pytest.approx(40.)
    none,reason=matcher.match(
        50.,50.,band="g",neighbor_search_radius_pix=22.)
    assert none is None and reason=="TRACTOR_NEIGHBOR_WITHIN_PSF_CUTOUT"
    none,reason=matcher.match(130.,90.,band="g")
    assert none is None and reason=="TRACTOR_LOW_BAND_SIGNAL_TO_NOISE"
