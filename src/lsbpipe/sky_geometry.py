"""Celestial north-through-east PA -> local pixel ellipse.

The affine map is estimated at a specified celestial center. It handles
rotated, mirrored and unequal pixel scales. Curved sky ellipses and large
WCS distortion are deliberately outside this local-linear approximation.

The algebraic core only uses NumPy and is directly regression-testable.
"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
from .geometry import ReferenceGeometry


@dataclass(frozen=True)
class LocalSkyEllipse:
    """Local tangent-plane ellipse represented by a pixel-space ellipse.

    pixel_a_per_sky_arcsec: pixel semi-major axis for sky semi-major 1 arcsec.
    pixel_area_arcsec2: local area of one image pixel (sky projected).
    """
    pixel: ReferenceGeometry
    pixel_a_per_sky_arcsec: float
    pixel_area_arcsec2: float
    sky_q: float
    sky_pa_deg: float
    local_pixel_per_arcsec_en: np.ndarray

    def edges_pix(self, edges_arcsec):
        edges = np.asarray(edges_arcsec, dtype=float)
        if edges.ndim != 1 or not np.all(np.isfinite(edges)) or np.any(edges < 0):
            raise ValueError("Sky aperture radii must be finite and non-negative")
        return edges * self.pixel_a_per_sky_arcsec


def ellipse_from_jacobian(x_pix: float, y_pix: float, sky_q: float,
                          sky_pa_deg: float, pixel_per_arcsec_en: np.ndarray) -> LocalSkyEllipse:
    """Transform a sky ellipse through a general local 2D affine WCS.

    The matrix is 2x2 and maps [delta east, delta north] (arcsec)
    into [delta x, delta y] pixels. PA is measured N through E, modulo 180.
    Singular values give pixel semiaxes for a unit semi-major sky ellipse.
    """
    if not np.isfinite(sky_pa_deg):
        raise ValueError("Sky PA must be finite")
    if not np.isfinite(sky_q) or not 0 < sky_q <= 1:
        raise ValueError("Sky axis ratio must satisfy 0 < q <= 1")
    k = np.asarray(pixel_per_arcsec_en, dtype=float)
    if k.shape != (2, 2) or not np.all(np.isfinite(k)):
        raise ValueError("Local WCS Jacobian must be finite 2x2 matrix")
    determinant = float(np.linalg.det(k))
    if abs(determinant) < 1e-10 or np.linalg.cond(k) > 1e8:
        raise ValueError("WCS matrix is singular or excessively ill-conditioned")
    pa = np.deg2rad(sky_pa_deg % 180.0)
    # In the EN plane, columns are unit major and minor direction vectors.
    basis = np.array([[np.sin(pa), np.cos(pa)],
                      [np.cos(pa), -np.sin(pa)]], dtype=float)
    unit_ellipse_map = k @ basis @ np.diag([1.0, sky_q])
    u, sv, _ = np.linalg.svd(unit_ellipse_map)
    major_vector = u[:, 0]
    a_per_arcsec = float(sv[0])
    q_pix = float(sv[1] / sv[0])
    theta_deg = float(np.degrees(np.arctan2(major_vector[1], major_vector[0])) % 180.0)
    pixel_area_arcsec2 = 1.0 / abs(determinant)
    return LocalSkyEllipse(
        pixel=ReferenceGeometry(x_pix=x_pix, y_pix=y_pix, q=q_pix, theta_image_deg=theta_deg),
        pixel_a_per_sky_arcsec=a_per_arcsec,
        pixel_area_arcsec2=pixel_area_arcsec2,
        sky_q=float(sky_q), sky_pa_deg=float(sky_pa_deg % 180.0),
        local_pixel_per_arcsec_en=k.copy(),
    )


def ellipse_from_wcs(wcs, ra_deg: float, dec_deg: float, *,
                     sky_q: float, sky_pa_deg: float, step_arcsec: float = 1.0) -> LocalSkyEllipse:
    """Local sky ellipse centered at (RA,Dec) using Astropy celestial WCS.

    Derivatives are symmetric finite differences in true sky directions, not
    differences in RA/Dec degrees (which fail badly near the poles). This
    conversion uses the tangent-plane Jacobian at the target center.
    """
    try:
        from astropy.coordinates import SkyCoord
        import astropy.units as u
    except ImportError as exc:
        raise RuntimeError("Astropy is required for WCS: pip install -e '.[astro]'") from exc
    if not np.isfinite(step_arcsec) or step_arcsec <= 0 or step_arcsec > 15:
        raise ValueError("step_arcsec must be in (0,15]")
    center = SkyCoord(ra_deg*u.deg, dec_deg*u.deg, frame='icrs')
    x, y = wcs.celestial.world_to_pixel(center)
    if not np.all(np.isfinite([x, y])):
        raise ValueError("Target celestial coordinate outside valid WCS")
    columns = []
    for position_angle in (90, 0):  # east, north
        direction = position_angle * u.deg
        c_plus = center.directional_offset_by(direction, step_arcsec*u.arcsec)
        c_minus = center.directional_offset_by(direction+180*u.deg, step_arcsec*u.arcsec)
        xp, yp = wcs.celestial.world_to_pixel(c_plus)
        xm, ym = wcs.celestial.world_to_pixel(c_minus)
        columns.append([(xp-xm)/(2*step_arcsec), (yp-ym)/(2*step_arcsec)])
    return ellipse_from_jacobian(float(x), float(y), sky_q, sky_pa_deg,
                                 np.array(columns).T)
