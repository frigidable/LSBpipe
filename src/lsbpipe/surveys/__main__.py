"""Headless survey explorer: the same archive logic as the desktop GUI."""
from __future__ import annotations
import argparse
import json
from pathlib import Path

from .legacy import SkyField, build_manifest, discover_bricks, save_manifest
from .transfer import probe_product, download_manifest


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m lsbpipe.surveys",
        description="Discover Legacy Survey bricks and prepare original FITS downloads")
    sub = parser.add_subparsers(dest="action", required=True)
    discover = sub.add_parser("discover", help="query cached/remote release brick catalogs")
    discover.add_argument("--ra", type=float, required=True)
    discover.add_argument("--dec", type=float, required=True)
    discover.add_argument("--width-arcmin", type=float, default=15.)
    discover.add_argument("--release", choices=("dr9","dr10","dr11"),
                          action="append", required=True)
    discover.add_argument("--output", type=Path, required=True)
    probe = sub.add_parser("probe", help="verify candidate file URLs via HTTP HEAD")
    probe.add_argument("--manifest", type=Path, required=True)
    probe.add_argument("--output", type=Path, required=True)
    get = sub.add_parser("download", help="download manifest product URLs to a local directory")
    get.add_argument("--manifest", type=Path, required=True)
    get.add_argument("--directory", type=Path, required=True)
    check = sub.add_parser("inspect-coverage",
        help="evaluate target and sky-annulus pixel coverage in a downloaded brick")
    check.add_argument("--directory", type=Path, required=True)
    check.add_argument("--release", choices=("dr9", "dr10", "dr11"), required=True)
    check.add_argument("--region", choices=("north", "south"), required=True)
    check.add_argument("--brick", required=True)
    check.add_argument("--band", choices=("g", "r", "i", "z"), required=True)
    check.add_argument("--ra", type=float, required=True)
    check.add_argument("--dec", type=float, required=True)
    check.add_argument("--target-radius", type=float, required=True)
    check.add_argument("--sky-inner", type=float, required=True)
    check.add_argument("--sky-outer", type=float, required=True)
    check.add_argument("--output", type=Path, required=True)
    pilot = sub.add_parser(
        "pilot", help="plan four real Legacy FITS for one explicit region/brick")
    pilot.add_argument("--ra", type=float, required=True)
    pilot.add_argument("--dec", type=float, required=True)
    pilot.add_argument("--width-arcmin", type=float, default=5.)
    pilot.add_argument("--release", choices=("dr9", "dr10", "dr11"),
                       default="dr9")
    pilot.add_argument("--region", choices=("north", "south"), required=True)
    pilot.add_argument("--brick", required=True)
    pilot.add_argument("--band", choices=("g", "r", "i", "z"), default="r")
    pilot.add_argument("--directory", type=Path, required=True)
    pilot.add_argument("--check-urls", action="store_true",
                       help="HEAD check each of the exactly four URLs")
    pilot.add_argument("--download", action="store_true",
                       help="explicitly download original four FITS to data/")
    pilot.add_argument("--inspect", action="store_true",
                       help="inspect local science quartet after download or from data/")
    pilot.add_argument("--inspect-square", action="store_true",
                       help="measure actual full search square on local FITS (no re-download)")
    pilot.add_argument("--target-radius", type=float, default=35.)
    pilot.add_argument("--sky-inner", type=float, default=65.)
    pilot.add_argument("--sky-outer", type=float, default=110.)
    args = parser.parse_args(argv)

    if args.action == "discover":
        field = SkyField(args.ra, args.dec, args.width_arcmin)
        manifest = build_manifest(field, discover_bricks(field, args.release))
        save_manifest(manifest, args.output)
        print(f"{manifest['brick_count']} brick hits; {len(manifest['products'])} unverified files")
        print(f"Candidate manifest: {args.output}")
    elif args.action == "probe":
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        if manifest.get("schema") != "lsbpipe.legacy-download/0.1":
            raise ValueError("Unsupported manifest schema")
        for entry in manifest["products"]:
            entry.update(probe_product(entry))
        save_manifest(manifest, args.output)
        statuses = {}
        for entry in manifest["products"]:
            statuses[entry["evidence"]] = statuses.get(entry["evidence"], 0) + 1
        print(json.dumps(statuses, indent=2))
    elif args.action == "download":
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        report = download_manifest(manifest, args.directory)
        successes = sum(item["state"] != "ERROR" for item in report["results"])
        print(f"{successes}/{len(report['results'])} completed; report at {args.directory / 'download-report.json'}")
        if not report["complete"]:
            return 1
    elif args.action == "pilot":
        from .science_pilot import run_science_pilot
        field = SkyField(args.ra, args.dec, args.width_arcmin)
        hits = discover_bricks(field, (args.release,))
        report = run_science_pilot(
            field, hits, release=args.release, region=args.region,
            brick=args.brick, band=args.band, directory=args.directory,
            check_urls=args.check_urls, download=args.download,
            inspect=args.inspect, inspect_square=args.inspect_square,
            target_radius_arcsec=args.target_radius,
            sky_inner_arcsec=args.sky_inner, sky_outer_arcsec=args.sky_outer)
        print(f"Pilot: {report['state']}")
        print(f"Products: {len(report['selected_products'])}")
        print(f"HTTP evidence: {json.dumps(report['http_evidence'])}")
        print(f"Report: {args.directory / 'pilot-report.json'}")
    elif args.action == "inspect-coverage":
        from .coverage import inspect_local_brick, save_coverage
        report = inspect_local_brick(
            args.directory,release=args.release,region=args.region,
            brick=args.brick,band=args.band,
            ra_deg=args.ra,dec_deg=args.dec,
            target_radius_arcsec=args.target_radius,
            sky_inner_arcsec=args.sky_inner,
            sky_outer_arcsec=args.sky_outer)
        save_coverage(report,args.output)
        print("Status:", report.status)
        for area in report.regions:
            print(f"{area.label}: sampled={area.pixel_centers_sampled}, "
                  f"usable={area.usable_fraction}, "
                  f"footprint_approx={area.estimated_footprint_fraction}")
        if report.warnings:
            print("Warnings:", "; ".join(report.warnings))
        print("Report:", args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
