"""Dependency-free verification of generated FITS header/data layout.

Not an independent replacement for astropy.io.fits integration tests.
"""
from importlib.util import spec_from_file_location, module_from_spec
from pathlib import Path
import numpy as np


def test_minimal_synthetic_primary_fits_header_and_payload(tmp_path):
    script=Path(__file__).parent.parent/'scripts/make_mock_legacy_fits.py'
    spec=spec_from_file_location('mkfixture',script)
    mod=module_from_spec(spec);spec.loader.exec_module(mod)
    mod.generate(tmp_path,size=64)
    for f,bitpix,dtype in [('mock-image-r.fits',-32,'>f4'),
                            ('mock-invvar-r.fits',-32,'>f4'),
                            ('mock-maskbits.fits',16,'>i2')]:
        payload=(tmp_path/f).read_bytes()
        assert len(payload)%2880==0
        cards=[payload[i:i+80].decode('ascii') for i in range(0,2880,80)]
        assert cards[0].startswith('SIMPLE')
        assert any(c.startswith('CTYPE1') and 'RA---TAN' in c for c in cards)
        assert any(c.startswith('CTYPE2') and 'DEC--TAN' in c for c in cards)
        assert any(c.startswith('BITPIX') and str(bitpix) in c for c in cards)
        arr=np.frombuffer(payload,dtype=dtype,count=64*64,offset=2880).reshape(64,64)
        assert arr.shape==(64,64)
        if 'maskbits' in f:
            assert arr[55,61] & (1<<3)
        else:
            assert np.all(np.isfinite(arr))
