import numpy as np
import pytest
from lsbpipe.survey import legacy_validity, named_bit, maskbits_to_uint, ObservationSet


def test_band_masks_semantics_and_do_not_erase_target():
    image=np.ones((5,6)); ivar=np.ones_like(image)
    bits=np.zeros(image.shape,dtype=np.int16)
    bits[0,0] |= (1<<12) # galaxy should remain usable
    bits[0,1] |= (1<<1)  # bright warning, not auto rejection
    bits[0,2] |= (1<<3)  # SATUR_R
    bits[0,3] |= (1<<6)  # ALLMASK_R
    bits[0,4] |= (1<<2)  # SATUR_G; no r effect
    ivar[1,1]=0
    mask,why=legacy_validity(image,ivar,bits,'r')
    assert mask[0,0] and mask[0,1] and mask[0,4]
    assert not mask[0,2] and not mask[0,3] and not mask[1,1]
    assert why['GALAXY'][0,0] and why['BRIGHT'][0,1]
    without, _=legacy_validity(image,ivar,bits,'r',reject_satur=False,reject_allmask=False)
    assert without[0,2] and without[0,3]
    gmask,_=legacy_validity(image,ivar,bits,'g')
    assert not gmask[0,4] and gmask[0,2] and gmask[0,3]


def test_signed_int16_maskbits_high_bit():
    raw=np.array([[-32768]],dtype=np.int16)
    assert maskbits_to_uint(raw)[0,0] == 32768
    assert named_bit(raw,'ALLMASK_I')[0,0]
    with pytest.raises(KeyError):named_bit(raw,'UNKNOWN_BIT')


def test_invalid_ivar_and_nonfinite_and_shape_errors():
    arr=np.ones((3,3)); bad=arr.copy();bad[0,0]=np.nan
    ivar=arr.copy();ivar[0,1]=-1
    good,_=legacy_validity(bad,ivar,None,'r')
    assert not good[0,0] and not good[0,1]
    with pytest.raises(ValueError):legacy_validity(arr,np.zeros((2,2)),None,'r')
    with pytest.raises(ValueError):legacy_validity(arr,arr,np.zeros((2,2),dtype='i2'),'r')


def test_observation_data_copy_immutability():
    data=np.ones((3,3));iv=np.ones((3,3))
    valid,_=legacy_validity(data,iv,None,'r')
    o=ObservationSet(data,iv,None,object(),'r','dr9',.262**2,valid,{})
    data[0,0]=5
    assert o.image_nMgy[0,0]==1
    with pytest.raises(ValueError):o.image_nMgy[0,0]=6
    assert o.measurement_frame().pixel_scale_arcsec==pytest.approx(.262)
