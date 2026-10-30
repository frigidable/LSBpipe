"""LSBpipe v0.2: FITS/WCS prototype; not a publication-ready survey pipeline."""
from .geometry import ReferenceGeometry, fractional_aperture_weights
from .profiles import MeasurementFrame, ProfileResult, measure_profiles, half_flux_radius
from .simulation import SersicTruth, sersic_image, analytic_sersic_flux

__version__ = "0.2.0"
__all__ = [
    "ReferenceGeometry", "fractional_aperture_weights", "MeasurementFrame",
    "ProfileResult", "measure_profiles", "half_flux_radius", "SersicTruth",
    "sersic_image", "analytic_sersic_flux",
]

from .sky_geometry import LocalSkyEllipse, ellipse_from_jacobian, ellipse_from_wcs
from .survey import ObservationSet, load_legacy_coadd, legacy_validity, named_bit
from .fits_workflow import measure_fits_observation, export_fits_measurement
