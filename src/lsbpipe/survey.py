"""Strict image ingestion and semantic Legacy Survey DR9/DR11 bitmask policy.

No network calls are made. FITS I/O requires Astropy. WCS mismatch fails
closed instead of assuming that equal array shapes imply co-registration.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping
import hashlib
import numpy as np
from .profiles import MeasurementFrame
from .sky_geometry import LocalSkyEllipse, ellipse_from_wcs

# Per-band definitions from Legacy Surveys DR9 bitmask documentation.
# Bits are zero-indexed, NOT powers of two.
LEGACY_BITS = {
    'NPRIMARY': 0, 'BRIGHT': 1, 'SATUR_G': 2, 'SATUR_R': 3,
    'SATUR_Z': 4, 'ALLMASK_G': 5, 'ALLMASK_R': 6,
    'ALLMASK_Z': 7, 'WISEM1': 8, 'WISEM2': 9,
    'BAILOUT': 10, 'MEDIUM': 11, 'GALAXY': 12,
    'CLUSTER': 13, 'SATUR_I': 14, 'ALLMASK_I': 15,
    'SUB_BLOB': 16, 'RESOLVED': 17, 'MCLOUDS': 18, 'WISE_GAIA': 19,
}


def _freeze_array(arr, dtype=None):
    value = np.array(arr, dtype=dtype, copy=True)
    value.setflags(write=False)
    return value


def maskbits_to_uint(maskbits):
    raw = np.asarray(maskbits)
    if raw.dtype.kind not in 'iu' or raw.dtype.itemsize > 4:
        raise ValueError('MASKBITS must contain 16- or 32-bit integers')
    # Convert signed int16 as raw 16 bits: 32768 may read as -32768.
    if raw.dtype.itemsize <= 2:
        return (raw.astype(np.int64) & 0xffff).astype(np.uint32)
    if np.any(raw < 0):
        raise ValueError('Negative 32-bit MASKBITS unsupported')
    return raw.astype(np.uint32)


def named_bit(maskbits, name: str) -> np.ndarray:
    if name not in LEGACY_BITS:
        raise KeyError(f'Unknown Legacy bit: {name}')
    return (maskbits_to_uint(maskbits) & np.uint32(1 << LEGACY_BITS[name])) != 0


def legacy_validity(image, invvar, maskbits, band: str, *,
                    reject_satur: bool = True, reject_allmask: bool = True,
                    custom_exclusion=None):
    """Create per-band valid mask and reasons; never reject BRIGHT/GALAXY blindly.

    SATUR_B and ALLMASK_B are default conservative exclusions, but SATUR_B
    means at least one input exposure was saturated and may overmask a coadd.
    Results should be recalculated with alternative quality policies later.
    Missing/zero ivar ALWAYS excludes the pixel.
    """
    band = band.lower()
    if band not in 'grzi' or len(band) != 1:
        raise ValueError('band must be one of g,r,z,i')
    data, ivar = np.asarray(image), np.asarray(invvar)
    if data.shape != ivar.shape or data.ndim != 2:
        raise ValueError('image and inverse variance must be same 2D shape')
    invalid_img = ~np.isfinite(data)
    invalid_ivar = ~np.isfinite(ivar) | (ivar <= 0)
    rejected = invalid_img | invalid_ivar
    reasons = {'NONFINITE_IMAGE': invalid_img, 'BAD_INVERSE_VARIANCE': invalid_ivar}
    if maskbits is not None:
        bits = maskbits_to_uint(maskbits)
        if bits.shape != data.shape:
            raise ValueError('maskbits shape mismatch')
        for prefix, enabled in (('SATUR', reject_satur), ('ALLMASK', reject_allmask)):
            name = f'{prefix}_{band.upper()}'
            flag = named_bit(bits, name)
            reasons[name] = flag
            if enabled:
                rejected |= flag
        for name in ('BRIGHT', 'MEDIUM', 'GALAXY', 'BAILOUT', 'NPRIMARY',
                     'SUB_BLOB', 'RESOLVED', 'MCLOUDS', 'WISE_GAIA'):
            reasons[name] = named_bit(bits, name)
    if custom_exclusion is not None:
        extra = np.asarray(custom_exclusion, dtype=bool)
        if extra.shape != data.shape:
            raise ValueError('custom exclusion shape mismatch')
        rejected |= extra
        reasons['CUSTOM_EXCLUSION'] = extra
    return ~rejected, {k: _freeze_array(v, bool) for k,v in reasons.items()}


def _check_wcs_compatible(reference, other, shape, label: str, *, tolerance_pix: float = 1e-3):
    """Compare five actual sky tie-points; tolerates FITS header representation changes."""
    ny, nx = shape
    px = np.array([0., nx-1., (nx-1.)/2, 0., nx-1.])
    py = np.array([0., 0., (ny-1.)/2, ny-1., ny-1.])
    ra, dec = reference.all_pix2world(px, py, 0)
    xo, yo = other.all_world2pix(ra, dec, 0)
    delta = np.hypot(np.asarray(xo) - px, np.asarray(yo) - py)
    if not np.all(np.isfinite(delta)) or float(np.max(delta)) > tolerance_pix:
        raise ValueError(f'{label} WCS grid mismatch (max {float(np.nanmax(delta)):.4g} pix)')


def _load_image(path, hdu, name: str, *, cutout_center=None, cutout_size_pix=None):
    try:
        from astropy.io import fits
        from astropy.wcs import WCS
        from astropy.nddata import Cutout2D
        from astropy.coordinates import SkyCoord
        import astropy.units as u
    except ImportError as exc:
        raise RuntimeError("Reading FITS requires Astropy: pip install -e '.[astro]'") from exc
    if not Path(path).is_file():
        raise FileNotFoundError(path)
    with fits.open(path, memmap=True) as f:
        raw = f[hdu].data
        header = f[hdu].header.copy()
        if raw is None or raw.ndim != 2:
            raise ValueError(f'{name}: expected 2D FITS')
        try:
            w = WCS(header)
        except Exception as exc:
            raise ValueError(f'{name}: cannot parse celestial WCS') from exc
        if not w.has_celestial or w.pixel_n_dim != 2 or w.world_n_dim != 2:
            raise ValueError(f'{name}: missing/invalid 2D celestial WCS')
        if cutout_size_pix is not None:
            if (not isinstance(cutout_size_pix, (int, np.integer)) or
                    cutout_size_pix < 2):
                raise ValueError('cutout_size_pix must be integer >=2')
            if cutout_center is None or len(cutout_center) != 2:
                raise ValueError('cutout_center RA/Dec required when cropping')
            center = SkyCoord(float(cutout_center[0])*u.deg,
                              float(cutout_center[1])*u.deg, frame='icrs')
            fill = 0 if name == 'maskbits' else np.nan
            try:
                clip = Cutout2D(raw, center, (cutout_size_pix,cutout_size_pix),
                        wcs=w.celestial, mode='partial', fill_value=fill, copy=True)
            except Exception as exc:
                raise ValueError(f'{name}: cannot create sky-centered cutout') from exc
            data = np.array(clip.data, copy=True)
            w = clip.wcs
        else:
            data = np.array(raw, copy=True)
    return data, header, w.celestial


def _sha256_file(path):
    dig = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024*1024), b''):
            dig.update(chunk)
    return dig.hexdigest()


@dataclass(frozen=True)
class ObservationSet:
    """Single-band, internally co-registered image bundle; never implicit reprojection."""
    image_nMgy: np.ndarray
    inverse_variance_nMgy2: np.ndarray
    maskbits: np.ndarray | None
    wcs: object
    band: str
    release: str
    pixel_area_arcsec2: float
    valid: np.ndarray
    mask_reasons: Mapping[str, np.ndarray]
    provenance: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self):
        image = _freeze_array(self.image_nMgy, float)
        ivar = _freeze_array(self.inverse_variance_nMgy2, float)
        valid = _freeze_array(self.valid, bool)
        if image.ndim != 2 or image.size == 0 or image.shape != ivar.shape or image.shape != valid.shape:
            raise ValueError('Observation arrays require equal non-empty 2D shapes')
        if not np.isfinite(self.pixel_area_arcsec2) or self.pixel_area_arcsec2 <= 0:
            raise ValueError('Pixel area must be finite positive')
        if np.any(valid & (~np.isfinite(image) | ~np.isfinite(ivar) | (ivar <= 0))):
            raise ValueError('Valid pixels must have finite image and positive finite ivar')
        object.__setattr__(self, 'image_nMgy', image)
        object.__setattr__(self, 'inverse_variance_nMgy2', ivar)
        object.__setattr__(self, 'valid', valid)
        if self.maskbits is not None:
            mb = _freeze_array(maskbits_to_uint(self.maskbits), np.uint32)
            if mb.shape != image.shape:
                raise ValueError('maskbits shape mismatch')
            object.__setattr__(self, 'maskbits', mb)
        object.__setattr__(self, 'mask_reasons', {k: _freeze_array(v, bool) for k, v in self.mask_reasons.items()})

    @property
    def shape(self):
        return self.image_nMgy.shape

    def measurement_frame(self, *, background=0.0, neighbor=0.0, pixel_area_arcsec2=None) -> MeasurementFrame:
        # Geometric mean pixel scale preserves *area* for a local affine WCS.
        return MeasurementFrame(image=self.image_nMgy,
            pixel_scale_arcsec=np.sqrt(self.pixel_area_arcsec2 if pixel_area_arcsec2 is None else pixel_area_arcsec2),
            valid=self.valid, inverse_variance=self.inverse_variance_nMgy2,
            background=background, neighbor=neighbor)

    def sky_ellipse(self, ra_deg, dec_deg, *, q, pa_deg, step_arcsec=1.0) -> LocalSkyEllipse:
        return ellipse_from_wcs(self.wcs, ra_deg, dec_deg, sky_q=q,
                                sky_pa_deg=pa_deg, step_arcsec=step_arcsec)


def load_legacy_coadd(image_path, ivar_path, maskbits_path, *, band, release='dr9',
                       image_hdu=0, ivar_hdu=0, maskbits_hdu=0,
                       reject_satur=True, reject_allmask=True,
                       verify_sha256=True, cutout_center=None,
                       cutout_size_pix=None) -> ObservationSet:
    """Read co-registered Legacy image / invvar / maskbits FITS (no download).

    Legacy image in nanomaggies/pixel; invvar in 1/(nanomaggy)^2. Per-band
    masks are derived from shared MASKBITS. No reproject or sky subtraction.
    """
    if release not in ('dr9', 'dr10', 'dr11'):
        raise ValueError('release must be dr9/dr10/dr11')
    if band not in ('g','r','z','i'):
        raise ValueError('band must be g/r/z/i')
    if release == 'dr9' and band == 'i':
        raise ValueError('Legacy DR9 optical coadds do not support i-band')
    if cutout_size_pix is not None and cutout_center is None:
        raise ValueError('cutout_center must be given with cutout_size_pix')
    opts={'cutout_center':cutout_center, 'cutout_size_pix':cutout_size_pix}
    image, header, refw = _load_image(image_path, image_hdu, 'image', **opts)
    ivar, _, iw = _load_image(ivar_path, ivar_hdu, 'inverse variance', **opts)
    bits, _, bw = _load_image(maskbits_path, maskbits_hdu, 'maskbits', **opts)
    if image.shape != ivar.shape or image.shape != bits.shape:
        raise ValueError('Image, ivar and maskbits array shapes differ')
    _check_wcs_compatible(refw, iw, image.shape, 'inverse variance')
    _check_wcs_compatible(refw, bw, image.shape, 'maskbits')
    unit = str(header.get('BUNIT','')).strip().lower().replace(' ', '')
    if unit and unit not in ('nanomaggies', 'nanomaggy', 'nmgy', 'nanomaggies/pixel', 'nanomaggy/pixel', 'nmgy/pixel'):
        raise ValueError(f'Unexpected FITS BUNIT {header["BUNIT"]!r}; expected nanomaggies/pixel')
    valid, reasons = legacy_validity(image, ivar, bits, band,
                                    reject_satur=reject_satur, reject_allmask=reject_allmask)
    center = refw.pixel_to_world((image.shape[1]-1)/2, (image.shape[0]-1)/2)
    local = ellipse_from_wcs(refw, center.ra.deg, center.dec.deg, sky_q=1., sky_pa_deg=0.)
    files = {'image': str(Path(image_path).resolve()), 'ivar': str(Path(ivar_path).resolve()),
             'maskbits': str(Path(maskbits_path).resolve())}
    provenance = {'survey':'Legacy Surveys', 'release':release, 'band':band,
        'files': files, 'sha256': {k:_sha256_file(v) for k,v in files.items()} if verify_sha256 else {},
        'unit': 'nanomaggies/pixel', 'inverse_variance_unit': '1/nanomaggies^2',
        'wcs_comparison_max_pix': 1e-3, 'background_state': 'upstream_sky_subtracted',
        'cutout_center_ra_dec': list(cutout_center) if cutout_center is not None else None,
        'cutout_size_pix':cutout_size_pix,
        'mask_policy': {'reject_satur': reject_satur, 'reject_allmask':reject_allmask,
                        'keep_bright_and_galaxy_as_diagnostic':True}}
    return ObservationSet(image_nMgy=image, inverse_variance_nMgy2=ivar,
                          maskbits=bits, wcs=refw, band=band, release=release,
                          pixel_area_arcsec2=local.pixel_area_arcsec2,
                          valid=valid, mask_reasons=reasons, provenance=provenance)
