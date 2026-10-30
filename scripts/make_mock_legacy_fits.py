"""Generate a small VALID but fully SYNTHETIC Legacy-style FITS triplet.

Writes simple uncompressed 2D primary HDUs using FITS 2880-byte alignment.
This is a *test fixture writer*, not an alternative scientific FITS I/O.
"""
from __future__ import annotations
import argparse
from pathlib import Path
import numpy as np
from lsbpipe.geometry import ReferenceGeometry
from lsbpipe.simulation import SersicTruth, sersic_image


def _card(name, value):
    if isinstance(value, bool):
        value_str = 'T' if value else 'F'
    elif isinstance(value, str):
        value_str = "'" + value.replace("'", "''") + "'"
    elif isinstance(value, int):
        value_str = str(value)
    else:
        value_str = f'{value:.13E}'
    return (f'{name:<8}= {value_str:>20}')[:80].ljust(80)


def write_primary_fits(filename, array, *, crval=(96.0619,77.9684), center=None, bunit=None):
    arr = np.asarray(array)
    if arr.ndim != 2:
        raise ValueError('2D required')
    dtype = '>f4' if arr.dtype.kind == 'f' else '>i2'
    bitpix = -32 if dtype == '>f4' else 16
    ny,nx = arr.shape
    cx,cy = center if center is not None else ((nx+1)/2, (ny+1)/2)
    cd = 0.262/3600.
    cards = [
       _card('SIMPLE',True), _card('BITPIX',bitpix),_card('NAXIS',2),
       _card('NAXIS1',nx),_card('NAXIS2',ny),_card('EXTEND',True),
       _card('CTYPE1','RA---TAN'),_card('CTYPE2','DEC--TAN'),
       _card('CRVAL1',float(crval[0])),_card('CRVAL2',float(crval[1])),
       _card('CRPIX1',float(cx)),_card('CRPIX2',float(cy)),
       _card('CD1_1',-cd),_card('CD1_2',0.0),
       _card('CD2_1',0.0),_card('CD2_2',cd),
       _card('ORIGIN','SYNTHETIC TEST FIXTURE'),
    ]
    if bunit is not None:
        cards.append(_card('BUNIT',bunit))
    header = (''.join(cards) + 'END'.ljust(80)).encode('ascii')
    header += b' ' * ((-len(header)) % 2880)
    raw = np.asarray(arr,dtype=dtype).tobytes(order='C')
    raw += b'\0'*((-len(raw)) % 2880)
    filename = Path(filename)
    filename.parent.mkdir(parents=True, exist_ok=True)
    filename.write_bytes(header + raw)


def generate(output:Path, *, size=128, seed=42):
    output = Path(output); output.mkdir(parents=True, exist_ok=True)
    center = (size-1)/2
    geom=ReferenceGeometry(center,center,0.63,37.)
    truth=SersicTruth(geom,re_pix=13,n=1,ie_nMgy_per_pix2=0.017)
    image=sersic_image((size,size),truth,subpixels=4)
    rng=np.random.default_rng(seed)
    sky=0.0003; noise=0.003
    data=(image + sky + rng.normal(0,noise,image.shape)).astype('f4')
    ivar=np.full((size,size), 1./noise**2, dtype='f4')
    ivar[12:16,22:27]=0
    bits=np.zeros((size,size),dtype='i2')
    bits[20:25,60:65] |= (1 << 1)    # BRIGHT informational only
    bits[30:40,30:40] |= (1 << 12)  # GALAXY informational only
    bits[55,61] |= (1 << 3)         # SATUR_R excluded
    bits[56,61] |= (1 << 6)         # ALLMASK_R excluded
    write_primary_fits(output/'mock-image-r.fits',data,bunit='nanomaggies/pixel')
    write_primary_fits(output/'mock-invvar-r.fits',ivar)
    write_primary_fits(output/'mock-maskbits.fits',bits)
    return output


if __name__ == '__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--output',type=Path,default=Path('mock_fits'))
    p.add_argument('--size',type=int,default=128)
    p.add_argument('--seed',type=int,default=42)
    a=p.parse_args()
    generate(a.output,size=a.size,seed=a.seed)
    print(f'SYNTHETIC FITS fixtures: {a.output}')
