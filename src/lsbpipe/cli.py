"""Reproducible synthetic demonstration; never represents real Legacy data."""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import platform
from dataclasses import asdict
from pathlib import Path
import numpy as np
from . import __version__
from .geometry import ReferenceGeometry
from .profiles import MeasurementFrame, measure_profiles, half_flux_radius
from .simulation import SersicTruth, sersic_image, analytic_sersic_flux


def demo(output: Path, *, seed: int, backend: str, subpixels: int) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    geom = ReferenceGeometry(x_pix=95.25, y_pix=95.7, q=0.67, theta_image_deg=34.0)
    truth = SersicTruth(geom, re_pix=20.0, n=1.0, ie_nMgy_per_pix2=.08)
    rng = np.random.default_rng(seed)
    clean = sersic_image((192, 192), truth, subpixels=6)
    sky, noise = .0004, .006
    observed = clean + sky + rng.normal(0, noise, clean.shape)
    yy, xx = np.indices(clean.shape)
    r = geom.elliptical_radius(xx, yy)
    phi = np.mod(np.arctan2(yy - geom.y_pix, xx - geom.x_pix), 2 * np.pi)
    masked = ~(((phi > np.deg2rad(25)) & (phi < np.deg2rad(90))) & (r > 13) & (r < 60))
    masked[62:69, 120:127] = False
    ivar = np.full(clean.shape, 1 / noise**2)
    edges = np.arange(0.0, 85.0, 2.0)
    all_frame = MeasurementFrame(observed, .262, inverse_variance=ivar, background=sky)
    cut_frame = MeasurementFrame(observed, .262, valid=masked, inverse_variance=ivar, background=sky)
    full = measure_profiles(all_frame, geom, edges, backend=backend, subpixels=subpixels)
    partial = measure_profiles(cut_frame, geom, edges, backend=backend, subpixels=subpixels)
    truth_cog = np.array([analytic_sersic_flux(truth, r) for r in edges[1:]])
    with (output / "profiles.csv").open("w", newline="", encoding="utf-8") as f:
        out = csv.writer(f)
        out.writerow(["a_inner_pix", "a_outer_pix", "flux_annulus_full_nMgy", "cog_full_nMgy", "cog_masked_nMgy", "analytic_cog_nMgy", "valid_fraction_masked", "mu_full_mag_arcsec2", "mu_masked_mag_arcsec2", "flags_masked"])
        for i in range(len(full.annular_flux_valid_nMgy)):
            out.writerow([edges[i], edges[i+1], full.annular_flux_valid_nMgy[i], full.cog_raw_nMgy[i], partial.cog_raw_nMgy[i], truth_cog[i], partial.area_fraction[i], full.mu_ab_mag_arcsec2[i], partial.mu_ab_mag_arcsec2[i], partial.flags[i]])
    np.savez_compressed(output / "synthetic_scene.npz", image=observed, intrinsic=clean, valid_mask=masked, inverse_variance=ivar, background_nMgy_per_pixel=np.array(sky), edges_pix=edges)
    try:
        estimated_re = half_flux_radius(full, target_flux_nMgy=full.cog_raw_nMgy[-1])
    except ValueError:
        estimated_re = None
    metrics = {
        "analytic_F_total_nMgy": truth.total_nMgy,
        "analytic_F_within_Rlim_nMgy": analytic_sersic_flux(truth, edges[-1]),
        "measured_F_within_Rlim_nMgy": float(full.cog_raw_nMgy[-1]),
        "masked_F_valid_within_Rlim_nMgy": float(partial.cog_raw_nMgy[-1]),
        "finite_aperture_R50_pix": estimated_re,
        "truth_intrinsic_Re_pix": truth.re_pix,
        "mean_abs_fractional_COG_error": float(np.mean(np.abs((full.cog_raw_nMgy - truth_cog) / truth_cog))),
        "telescope_data": False,
    }
    manifest = {
        "name": "SYNTHETIC_DEMO_NOT_NGC2146SAT",
        "version": __version__, "seed": seed, "backend": backend,
        "subpixels": subpixels, "pixel_scale_arcsec": .262,
        "geometry": asdict(geom), "truth": {"Re_pix": truth.re_pix, "n": truth.n, "Ie": truth.ie_nMgy_per_pix2},
        "known_sky_nMgy_per_pixel": sky, "known_noise_rms_nMgy_per_pixel": noise,
        "python": platform.python_version(),
        "input_sha256": hashlib.sha256(observed.tobytes()).hexdigest(),
        "metrics": metrics,
        "limitations": ["no FITS/WCS", "no PSF", "no automated sky estimate", "diagonal-only variance", "no reconstruction", "synthetic source only"],
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return metrics
    fig, ax = plt.subplots(figsize=(6.4, 5.3))
    ax.imshow(observed - sky, origin="lower", norm="asinh")
    ax.contour(~masked, levels=[0.5], linewidths=[.8])
    ax.set(xlabel="Column x (pixel)", ylabel="Row y (pixel)", title="Synthetic Sérsic galaxy: masked sector (outline)")
    fig.tight_layout()
    fig.savefig(output / "demo_scene.png", dpi=160)
    plt.close(fig)
    fig, ax = plt.subplots(figsize=(7, 4.6))
    ax.plot(edges[1:], full.cog_raw_nMgy, label="Measured: unmasked")
    ax.plot(edges[1:], partial.cog_raw_nMgy, label="Observed pixels: masked")
    ax.plot(edges[1:], truth_cog, linestyle="--", label="Analytic truth")
    ax.axvline(truth.re_pix, linestyle=":", label="Intrinsic Re (truth)")
    ax.set(xlabel="Semi-major axis (pixel)", ylabel="Cumulative flux (nanomaggies)", title="Raw curve of growth: no masked-flux reconstruction")
    ax.legend()
    fig.tight_layout()
    fig.savefig(output / "demo_growth.png", dpi=160)
    plt.close(fig)
    fig, ax = plt.subplots(figsize=(7,4.6))
    rmid = .5*(edges[:-1] + edges[1:])
    ax.plot(rmid, full.intensity_nMgy_arcsec2, label="Unmasked")
    ax.plot(rmid, partial.intensity_nMgy_arcsec2, label="Masked, per valid area")
    ax.axhline(0, linestyle=":")
    ax.set(xlabel="Semi-major axis (pixel)", ylabel="Mean intensity (nMgy / arcsec²)", title="Signed annular surface intensity")
    ax.legend()
    fig.tight_layout()
    fig.savefig(output / "demo_profile.png", dpi=160)
    plt.close(fig)
    return metrics


def main(argv=None):
    p = argparse.ArgumentParser(prog="lsbpipe", description="LSBpipe v0.2 prototype: synthetic demonstration and local Legacy FITS measurements")
    cmd = p.add_subparsers(dest="command", required=True)
    sim = cmd.add_parser("demo", help="generate reproducible synthetic galaxy and diagnostics")
    sim.add_argument("--output", type=Path, default=Path("demo_output"))
    sim.add_argument("--seed", type=int, default=42)
    sim.add_argument("--backend", choices=["subpixel", "exact"], default="subpixel")
    sim.add_argument("--subpixels", type=int, default=8)
    fits_cmd = cmd.add_parser('measure-fits', help='measure local sky ellipse on existing Legacy coadd FITS (requires astropy + photutils for exact)')
    fits_cmd.add_argument('--image', type=Path, required=True)
    fits_cmd.add_argument('--ivar', type=Path, required=True)
    fits_cmd.add_argument('--maskbits', type=Path, required=True)
    fits_cmd.add_argument('--band', choices=['g','r','i','z'], required=True)
    fits_cmd.add_argument('--release',choices=['dr9','dr10','dr11'],default='dr9')
    fits_cmd.add_argument('--image-hdu',type=int,default=0)
    fits_cmd.add_argument('--ivar-hdu',type=int,default=0)
    fits_cmd.add_argument('--maskbits-hdu',type=int,default=0)
    fits_cmd.add_argument('--ra',type=float, required=True)
    fits_cmd.add_argument('--dec',type=float, required=True)
    fits_cmd.add_argument('--q',type=float, required=True)
    fits_cmd.add_argument('--pa',type=float, required=True,help='celestial PA degrees from north through east')
    fits_cmd.add_argument('--max-radius',type=float,required=True,help='arcsec')
    fits_cmd.add_argument('--step',type=float,default=3.,help='radial step arcsec')
    fits_cmd.add_argument('--background',type=float,default=0.,help='caller-supplied residual sky nMgy/pixel; NOT estimated')
    fits_cmd.add_argument('--backend',choices=['exact','subpixel'],default='exact')
    fits_cmd.add_argument('--subpixels',type=int,default=12)
    fits_cmd.add_argument('--output',type=Path,required=True)
    fits_cmd.add_argument('--cutout-size',type=int,default=None,
          help='crop each source FITS around (--ra,--dec) before reading entire brick')
    fits_cmd.add_argument('--keep-satur',action='store_true',
          help='retain SATUR_band pixels as an alternate mask policy')
    fits_cmd.add_argument('--keep-allmask',action='store_true',
          help='retain ALLMASK_band pixels as an alternate mask policy')
    args = p.parse_args(argv)
    if args.command == 'measure-fits':
        from .survey import load_legacy_coadd
        from .fits_workflow import measure_fits_observation, export_fits_measurement
        o=load_legacy_coadd(args.image,args.ivar,args.maskbits,band=args.band,release=args.release,
           image_hdu=args.image_hdu,ivar_hdu=args.ivar_hdu,maskbits_hdu=args.maskbits_hdu,
           reject_satur=not args.keep_satur,reject_allmask=not args.keep_allmask,
           cutout_center=(args.ra,args.dec) if args.cutout_size else None,
           cutout_size_pix=args.cutout_size)
        m=measure_fits_observation(o,ra_deg=args.ra,dec_deg=args.dec,q=args.q,pa_deg=args.pa,
           radius_max_arcsec=args.max_radius,step_arcsec=args.step,
           background_nMgy_per_pixel=args.background,backend=args.backend,subpixels=args.subpixels)
        manifest=export_fits_measurement(m,args.output)
        print(json.dumps({'output':str(args.output), 'final_cog_valid_nMgy':manifest['measurement']['final_cog_valid_nMgy'],
                 'science_status':manifest['scientific_status']},indent=2))
        return
    if args.command == "demo":
        metrics = demo(args.output, seed=args.seed, backend=args.backend, subpixels=args.subpixels)
        print(json.dumps(metrics, indent=2))

if __name__ == "__main__":
    main()
