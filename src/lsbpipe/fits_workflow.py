"""One-band FITS -> sky ellipse -> raw rings/COG, without sky or PSF fitting."""
from __future__ import annotations
import csv
import json
from dataclasses import dataclass
from pathlib import Path
import numpy as np
from .survey import ObservationSet
from .profiles import ProfileResult, measure_profiles
from .sky_geometry import LocalSkyEllipse
from . import __version__


@dataclass(frozen=True)
class FitsPhotometry:
    observation: ObservationSet
    geometry: LocalSkyEllipse
    result: ProfileResult
    background_nMgy_per_pixel: float


def measure_fits_observation(observation: ObservationSet, *, ra_deg:float,
    dec_deg:float, q:float, pa_deg:float, radius_max_arcsec:float,
    step_arcsec:float, background_nMgy_per_pixel:float=0.,
    backend:str='exact', subpixels:int=12) -> FitsPhotometry:
    """No source modelling or mask reconstruction; background is user-supplied.

    External arrays are already in nanomaggies/pixel. Reported uncertainties
    only represent diagonal pixel variances (not coadd covariance or sky).
    """
    if not np.isfinite(radius_max_arcsec) or radius_max_arcsec <= 0:
        raise ValueError('radius_max_arcsec must be >0')
    if not np.isfinite(step_arcsec) or step_arcsec <= 0:
        raise ValueError('step_arcsec must be >0')
    if not np.isfinite(background_nMgy_per_pixel):
        raise ValueError('background must be finite')
    if np.ceil(radius_max_arcsec/step_arcsec) > 3000:
        raise ValueError('Too many annuli; increase radial step')
    sky_ellipse = observation.sky_ellipse(ra_deg, dec_deg, q=q, pa_deg=pa_deg)
    edges_sky = np.r_[np.arange(0., radius_max_arcsec, step_arcsec), radius_max_arcsec]
    edges_pix = sky_ellipse.edges_pix(edges_sky)
    frame = observation.measurement_frame(background=background_nMgy_per_pixel,
                 pixel_area_arcsec2=sky_ellipse.pixel_area_arcsec2)
    out = measure_profiles(frame, sky_ellipse.pixel, edges_pix,
                           backend=backend, subpixels=subpixels)
    return FitsPhotometry(observation, sky_ellipse, out, background_nMgy_per_pixel)


def export_fits_measurement(measurement: FitsPhotometry, out_dir) -> dict:
    """Write ECSV-like plain CSV and JSON manifest with non-asserted QA status."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    prof = measurement.result
    geo = measurement.geometry
    r_arcsec = prof.edges_pix / geo.pixel_a_per_sky_arcsec
    csv_path = out/'radial_profile.csv'
    with csv_path.open('w', newline='',encoding='utf-8') as f:
        writer=csv.writer(f)
        writer.writerow(['a_inner_arcsec','a_outer_arcsec','flux_valid_nMgy','cog_raw_nMgy',
             'annulus_sigma_diagonal_nMgy','cog_sigma_diagonal_nMgy',
             'area_geometric_pix2','area_valid_pix2','valid_area_fraction',
             'intensity_nMgy_per_arcsec2','mu_AB_mag_per_arcsec2','flags'])
        for i in range(len(prof.flags)):
            writer.writerow([r_arcsec[i], r_arcsec[i+1],prof.annular_flux_valid_nMgy[i],
                prof.cog_raw_nMgy[i], prof.annular_sigma_diagonal_nMgy[i],
                prof.cog_sigma_diagonal_nMgy[i],prof.area_geometric_pix2[i],
                prof.area_valid_pix2[i],prof.area_fraction[i],
                prof.intensity_nMgy_arcsec2[i],prof.mu_ab_mag_arcsec2[i],prof.flags[i]])
    manifest={'lsbpipe_version':__version__, 'input':dict(measurement.observation.provenance),
       'aperture':{'ra_deg':float(measurement.observation.wcs.pixel_to_world(
                        geo.pixel.x_pix,geo.pixel.y_pix).ra.deg),
                   'dec_deg':float(measurement.observation.wcs.pixel_to_world(
                        geo.pixel.x_pix,geo.pixel.y_pix).dec.deg),
                   'pa_sky_deg':geo.sky_pa_deg, 'q_sky':geo.sky_q,
                   'q_pixel':geo.pixel.q,'theta_pixel_deg':geo.pixel.theta_image_deg,
                   'x_pixel':geo.pixel.x_pix,'y_pixel':geo.pixel.y_pix,
                   'pixel_a_per_arcsec':geo.pixel_a_per_sky_arcsec,
                   'pixel_area_arcsec2':geo.pixel_area_arcsec2,
                   'local_pixel_per_arcsec_EN':geo.local_pixel_per_arcsec_en.tolist(),
                   'local_affine_approximation':True},
       'measurement':{'n_annuli':len(prof.flags),'radius_max_arcsec':float(r_arcsec[-1]),
                    'background_residual_nMgy_per_pixel':measurement.background_nMgy_per_pixel,
                    'backend':prof.backend, 'subpixels':prof.subpixels,
                    'final_cog_valid_nMgy':float(prof.cog_raw_nMgy[-1]),
                    'invalid_pixel_fraction':float(1-np.mean(measurement.observation.valid)),
                    'annular_flags':list(prof.flags)},
       'scientific_status':'EXPERIMENTAL_NOT_ADOPTED',
       'limitations':['Input Legacy coadd has upstream sky subtraction',
            'No independent sky model fitted: caller-supplied residual background only',
            'Pixel ivar yields only diagonal statistical variance; correlated noise excluded',
            'Local affine WCS ellipse approximation; wide-field distortion not assessed',
            'No masked flux reconstruction, no PSF, no star-halo correction',
            'Do not use raw masked COG as an asymptotic galaxy flux']}
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2,ensure_ascii=False),encoding='utf-8')
    return manifest
