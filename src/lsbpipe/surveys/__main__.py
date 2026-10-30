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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
