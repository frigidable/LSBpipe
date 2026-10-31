"""Inspect original Legacy FITS visually, without adopting galaxy photometry.

Run: python -m lsbpipe.surveys.qa_plots --help
This is a *single brick* display. It refuses partial cutouts rather than
inventing missing pixels; multi-brick mosaics require a separate workflow.
Matplotlib and Astropy are imported lazily and remain optional for the core.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from pathlib import Path

import numpy as np

from lsbpipe.survey import LEGACY_BITS, _check_wcs_compatible, maskbits_to_uint
from .coverage import _science_hdu_index
from .legacy import BrickHit, SkyField, product_for, regions_for_release
from .viewer import field_polygon


MASK_LABELS = (
    "MASKBITS = 0", "SGA GALAXY only",
    "Other MASKBITS flags", "SATUR/ALLMASK (band)"
)


def classify_maskbits(maskbits, band: str) -> np.ndarray:
    """Display categories with documented priority; not a rejection mask.

    For overlapping flags, instrumental SATUR/ALLMASK outrank warning
    flags, which outrank the SGA footprint. Individual flags remain
    available in the FITS and the numeric coverage JSON.
    """
    if band not in ("g", "r", "i", "z"):
        raise ValueError("Expected g/r/i/z band")
    flags = maskbits_to_uint(maskbits)
    classes = np.zeros(flags.shape, dtype=np.uint8)

    def has(name: str) -> np.ndarray:
        return (flags & np.uint32(1 << LEGACY_BITS[name])) != 0

    galaxy_mask = np.uint32(1 << LEGACY_BITS["GALAXY"])
    hard_mask = np.uint32(
        (1 << LEGACY_BITS["SATUR_" + band.upper()]) |
        (1 << LEGACY_BITS["ALLMASK_" + band.upper()]))
    classes[has("GALAXY")] = 1
    # Every OTHER nonzero bit is visible, even when not a chosen hard
    # exclusion. Includes different-band instrument flags, because
    # silently rendering those pixels as pristine would be misleading.
    other_mask = np.uint32(0xFFFFFFFF ^ int(galaxy_mask | hard_mask))
    classes[(flags & other_mask) != 0] = 2
    classes[(flags & hard_mask) != 0] = 3
    return classes


def _cutout_pixels(width_arcmin: float, wcs, *, padding: float = 1.60) -> int:
    """Conservative figure viewport, not the science angular footprint."""
    if not 0 < width_arcmin <= 120:
        raise ValueError("Invalid search field width")
    from astropy.wcs.utils import proj_plane_pixel_scales
    scales = proj_plane_pixel_scales(wcs.celestial) * 3600.
    if not np.all(np.isfinite(scales)) or np.any(scales <= 0):
        raise ValueError("Invalid FITS WCS pixel scales")
    # Even a rotated square fits: diagonal ratio sqrt(2), plus margin.
    pixels = int(np.ceil(width_arcmin * 60 * padding / min(scales)))
    if pixels > 4096:
        raise ValueError("Requested field exceeds conservative one-brick figure limit")
    return max(16, pixels)


def _world_path(wcs, world_pairs, *, center=None) -> tuple[np.ndarray, np.ndarray]:
    pts = np.asarray(world_pairs, dtype=float)
    xs, ys = wcs.all_world2pix(pts[:, 0], pts[:, 1], 0)
    return np.asarray(xs), np.asarray(ys)


def _circle_on_sky(ra_deg: float, dec_deg: float, radius_arcsec: float,
                   *, samples: int = 181) -> tuple[tuple[float, float], ...]:
    """Compute true angular-radius vertices rather than a pixel-space circle."""
    from astropy.coordinates import SkyCoord
    import astropy.units as u
    center = SkyCoord(ra_deg * u.deg, dec_deg * u.deg, frame="icrs")
    angles = np.linspace(0, 360, samples) * u.deg
    boundary = center.directional_offset_by(angles, radius_arcsec * u.arcsec)
    return tuple(zip(boundary.ra.deg, boundary.dec.deg))


def _load_four_cutouts(root: Path, *, field: SkyField,
                       release: str, region: str, brick: str, band: str):
    """Read four original, co-registered FITS into an identical strict cutout.

    A geometrically incomplete single-brick field fails, instead of being
    silently padded by zeros (particularly dangerous for maskbits).
    """
    from astropy.io import fits
    from astropy.nddata import Cutout2D
    from astropy.wcs import WCS
    from astropy.coordinates import SkyCoord
    import astropy.units as u

    if region not in regions_for_release(release):
        raise ValueError("Unsupported survey release/region")
    allowed = ("g", "r", "z") if region == "north" else ("g", "r", "i", "z")
    if band not in allowed:
        raise ValueError("Band is unavailable in selected release/region")
    hit = BrickHit(release, region, brick, 0, 0, 0, 0, {band: 1})
    products = {
        kind: Path(root) / product_for(
            hit, kind, None if kind == "maskbits" else band).relative_path
        for kind in ("image", "invvar", "nexp", "maskbits")
    }
    for kind, path in products.items():
        if not path.is_file():
            raise FileNotFoundError(f"Required {kind} FITS not found: {path}")

    with ExitStack() as stack:
        hdus = {
            k: stack.enter_context(fits.open(path, memmap=True))
            for k, path in products.items()
        }
        idx = {k: _science_hdu_index(hdus[k], k) for k in products}
        arrays = {k: hdus[k][idx[k]].data for k in products}
        ref_shape = arrays["image"].shape
        if any(array is None or array.ndim != 2 or array.shape != ref_shape
               for array in arrays.values()):
            raise ValueError("Science layers must have identical 2D shapes")
        wcs = {
            k: WCS(hdus[k][idx[k]].header).celestial for k in products
        }
        for kind in ("invvar", "nexp", "maskbits"):
            _check_wcs_compatible(wcs["image"], wcs[kind], ref_shape, kind)
        n = _cutout_pixels(field.width_arcmin, wcs["image"])
        center = SkyCoord(field.ra_deg * u.deg,
                          field.dec_deg * u.deg, frame="icrs")
        cutouts = {}
        for kind, array in arrays.items():
            try:
                cutout = Cutout2D(
                    array, center, (n, n), wcs=wcs["image"],
                    mode="strict", copy=True)
            except (ValueError, TypeError) as exc:
                raise ValueError(
                    "Requested plot cutout extends beyond this brick. "
                    "Use a multi-brick workflow; do not interpret padded "
                    "missing areas as valid pixels.") from exc
            cutouts[kind] = cutout
    return {k: obj.data for k, obj in cutouts.items()}, cutouts["image"].wcs, idx


def render_qa_figure(*, directory: Path, release: str, region: str,
                     brick: str, band: str, field: SkyField,
                     output: Path, target_radius_arcsec: float = 35.,
                     sky_inner_arcsec: float = 65.,
                     sky_outer_arcsec: float = 110.) -> Path:
    """Export four-panel WCS QA, annotation, and instrument/SGA mask legend."""
    if not 0 < target_radius_arcsec <= sky_inner_arcsec < sky_outer_arcsec:
        raise ValueError("Require 0 < target radius <= sky inner < sky outer")
    data, wcs, indices = _load_four_cutouts(
        directory, field=field, release=release, region=region,
        brick=brick, band=band)

    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import pyplot as plt
    from matplotlib.colors import BoundaryNorm, ListedColormap
    from astropy.visualization import AsinhStretch, ImageNormalize

    image, invvar, nexp, bits = (
        data[k] for k in ("image", "invvar", "nexp", "maskbits"))
    image_finite = image[np.isfinite(image)]
    if image_finite.size == 0:
        raise ValueError("No finite image flux in local cutout")
    lo, hi = np.percentile(image_finite, [1., 99.7])
    if hi <= lo:
        hi = float(lo) + 1.
    flux_norm = ImageNormalize(
        vmin=float(lo), vmax=float(hi), stretch=AsinhStretch(0.04))
    ivar_log = np.where(
        np.isfinite(invvar) & (invvar > 0),
        np.log10(np.maximum(invvar, np.finfo(float).tiny)), np.nan)
    classes = classify_maskbits(bits, band)

    fig, axes = plt.subplots(
        2, 2, figsize=(11, 10.2),
        subplot_kw={"projection": wcs}, layout="constrained")
    plot_axes = list(axes.flat)
    cmap = ListedColormap(["#ECECEC", "#527FB7", "#E6A645", "#C24B42"])
    norm = BoundaryNorm([-0.5, 0.5, 1.5, 2.5, 3.5], cmap.N)

    def show(ax, pixels, title, *, norm=None, colormap="viridis", label=None):
        im = ax.imshow(
            pixels, origin="lower", interpolation="nearest",
            cmap=colormap, norm=norm)
        ax.set_title(title, fontsize=11)
        ax.coords[0].set_axislabel("RA (ICRS)", fontsize=9)
        ax.coords[1].set_axislabel("Dec (ICRS)", fontsize=9)
        ax.tick_params(labelsize=7)
        if label is not None:
            colorbar = fig.colorbar(im, ax=ax, fraction=0.048, pad=0.01)
            colorbar.set_label(label, fontsize=8)
        return im

    show(plot_axes[0], image, f"Original IMAGE · {band}-band flux",
         norm=flux_norm, colormap="gray", label="nanomaggy")
    show(plot_axes[1], ivar_log, "Original INVVAR (log10)",
         label="log10 inverse (nanomaggy²)")
    show(plot_axes[2], np.ma.masked_where(
        ~np.isfinite(nexp) | (nexp <= 0), nexp),
        "NEXP · contributing exposures", label="Exposures")
    im = show(plot_axes[3], classes, "MASKBITS categories (diagnostic)",
              norm=norm, colormap=cmap)
    cb = fig.colorbar(im, ax=plot_axes[3], ticks=[0, 1, 2, 3],
                      fraction=0.048, pad=0.01)
    cb.ax.set_yticklabels(MASK_LABELS, fontsize=7)

    square = field_polygon(field)
    overlays = [
        (_world_path(wcs, square), "-", 1.3, "Search square"),
        (_world_path(wcs, _circle_on_sky(
            field.ra_deg, field.dec_deg, target_radius_arcsec)),
         "--", 0.9, "Target screening circle"),
        (_world_path(wcs, _circle_on_sky(
            field.ra_deg, field.dec_deg, sky_inner_arcsec)),
         ":", 0.9, "Sky annulus inner"),
        (_world_path(wcs, _circle_on_sky(
            field.ra_deg, field.dec_deg, sky_outer_arcsec)),
         ":", 0.9, "Sky annulus outer"),
    ]
    for ax in plot_axes:
        for (x, y), style, lw, _ in overlays:
            ax.plot(x, y, style, linewidth=lw, color="#FF33AA", alpha=0.90)
        ax.set_xlim(-0.5, image.shape[1]-0.5)
        ax.set_ylim(-0.5, image.shape[0]-0.5)

    fig.text(
        0.03, 0.008,
        f"ICRS ({field.ra_deg:.6f}°, {field.dec_deg:+.6f}°); "
        f"search side {field.width_arcmin:g}′; "
        f"DR {release.upper()} {region} / {brick} / {band}; "
        f"HDU {indices}. Magenta: sky-coordinate boundaries. "
        "QA only, no sky model, no PSF or science adoption.",
        fontsize=8)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        fig.savefig(output, dpi=140)
    finally:
        plt.close(fig)
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Plot original four-FITS WCS QA (diagnostic only)")
    parser.add_argument("--directory", type=Path, required=True,
                        help="Download root containing dr9/south/coadd/...")
    parser.add_argument("--release", choices=("dr9","dr10","dr11"),
                        default="dr9")
    parser.add_argument("--region", choices=("north", "south"),
                        required=True)
    parser.add_argument("--brick", required=True)
    parser.add_argument("--band", choices=("g", "r", "i", "z"), default="r")
    parser.add_argument("--ra", type=float, required=True)
    parser.add_argument("--dec", type=float, required=True)
    parser.add_argument("--width-arcmin", type=float, default=5.)
    parser.add_argument("--target-radius", type=float, default=35.)
    parser.add_argument("--sky-inner", type=float, default=65.)
    parser.add_argument("--sky-outer", type=float, default=110.)
    parser.add_argument("--output", type=Path, required=True)
    a = parser.parse_args(argv)
    result = render_qa_figure(
        directory=a.directory, release=a.release, region=a.region,
        brick=a.brick, band=a.band,
        field=SkyField(a.ra, a.dec, a.width_arcmin),
        target_radius_arcsec=a.target_radius,
        sky_inner_arcsec=a.sky_inner,
        sky_outer_arcsec=a.sky_outer,
        output=a.output)
    print(f"Saved original FITS QA plot: {result}")
    print("Diagnostic preview only; the full requested sky field is not "
          "certified complete by one brick.")


if __name__ == "__main__":
    main()
