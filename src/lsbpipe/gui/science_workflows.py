"""Local FITS science-QA orchestration for the desktop GUI.

Pure Python, no Qt dependency, no network operations and no FITS rewriting.
All results remain DIAGNOSTIC_NOT_ADOPTED. This adapter intentionally
reuses exactly the already tested scientific modules instead of embedding
a second implementation in the GUI.
"""
from __future__ import annotations

from pathlib import Path
import re

from lsbpipe.surveys.legacy import SkyField, BrickHit, product_for


ACTIONS = ("square", "masks", "figure", "compare")


def science_data_root(directory: str | Path, *, hit: BrickHit, band: str) -> Path:
    """Accept either the download root or a pilot folder containing data/.

    Never guess a different release/region. A valid root must contain
    the complete original four-product quartet for the selected brick.
    """
    root = Path(directory).expanduser()
    if not str(directory).strip():
        raise ValueError("Choose the folder containing downloaded original FITS")
    wanted = {
        kind: product_for(hit, kind, None if kind == "maskbits" else band)
        for kind in ("image", "invvar", "nexp", "maskbits")
    }
    for candidate in (root, root / "data"):
        if all((candidate / item.relative_path).is_file()
               for item in wanted.values()):
            return candidate
    missing = [item.relative_path for item in wanted.values()]
    raise FileNotFoundError(
        "The selected folder does not contain the complete "
        f"{hit.release.upper()} {hit.region} {hit.name} {band}-band "
        "original-FITS quartet (IMAGE, INVVAR, NEXP, MASKBITS). "
        "Choose the download data/ directory or its pilot parent. "
        "Expected paths, relative to data root: " + ", ".join(missing))


def _file_stem(field: SkyField, hit: BrickHit, band: str) -> str:
    # Keep separate contexts distinct even inside the same download root.
    identity = (f"{hit.release}_{hit.region}_{hit.name}_{band}_"
                f"{field.ra_deg:.10f}_{field.dec_deg:+.10f}_"
                f"{field.width_arcmin:.5f}arcmin")
    return re.sub(r"[^a-zA-Z0-9_.+-]", "_", identity)


def run_science_qa(
    action: str, *, field: SkyField, hit: BrickHit, band: str,
    directory: str | Path, north_directory: str | Path = "",
    south_directory: str | Path = "",
    target_radius_arcsec: float = 35.,
    sky_inner_arcsec: float = 65.,
    sky_outer_arcsec: float = 110.,
) -> dict:
    """Execute one selected action, returning readable summary and local path.

    Returned objects are immutable strings/numbers safe for signal passing
    back to the Qt main thread; all computations and file I/O occur in the
    calling worker thread. Neither data discovery nor network access occurs.
    """
    if action not in ACTIONS:
        raise ValueError(f"Unsupported science-QA operation: {action}")
    if band not in hit.median_exposures:
        raise ValueError(f"{band} band not in the selected brick summary")
    if action == "compare":
        if hit.release != "dr9":
            raise ValueError("Paired North/South comparison currently supports DR9 only")
        north = BrickHit("dr9", "north", hit.name, 0, 0, 0, 0, {band: 1})
        south = BrickHit("dr9", "south", hit.name, 0, 0, 0, 0, {band: 1})
        northern = science_data_root(north_directory, hit=north, band=band)
        southern = science_data_root(south_directory, hit=south, band=band)
        from lsbpipe.surveys.compare_regions import compare_original_regions
        from lsbpipe.surveys.legacy import SkyField
        report = compare_original_regions(
            northern, southern, field=field, brick=hit.name, band=band)
        destination = southern / "qa-reports" / (
            _file_stem(field, south, band) + "_north-south.json")
        destination.parent.mkdir(parents=True, exist_ok=True)
        import json
        destination.write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        joint = report["paired_instrumental_validity"]
        lines = [
            "DR9 North / South · paired original pixels",
            f"Same WCS grid: {report['native_wcs_alignment']}",
            f"Common sky pixels: {report['pixels']:,}",
            f"Valid in both: {joint['both']:,}",
            f"South only: {joint['south_only']:,}  ·  North only: {joint['north_only']:,}",
            f"Invalid in both: {joint['neither']:,}",
            "No stacking, independent-noise claim or photometric adoption.",
        ]
    else:
        data_root = science_data_root(directory, hit=hit, band=band)
        destination = data_root / "qa-reports" / _file_stem(field, hit, band)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if action == "square":
            from lsbpipe.surveys.square_coverage import (
                inspect_local_square, save_square_coverage)
            result = inspect_local_square(
                data_root, release=hit.release, region=hit.region,
                brick=hit.name, band=band, field=field)
            destination = destination.with_name(destination.name + "_square.json")
            save_square_coverage(result, destination)
            total = result.pixel_centers_sampled
            frac = result.instrumentally_usable_fraction
            lines = [
                f"{hit.release.upper()} {hit.region} {hit.name} · {band} band",
                f"Selected square: {field.width_arcmin:g}′ × {field.width_arcmin:g}′",
                f"Inside one brick: {'yes' if result.polygon_within_single_brick else 'NO — incomplete'}",
                f"Pixel centers: {total:,}",
                f"Instrumentally usable: {100*frac:.3f}%" if frac is not None
                    else "Instrumentally usable: no sampled pixels",
                f"Mean positive NEXP: {result.mean_nexp_where_positive:.2f}"
                    if result.mean_nexp_where_positive is not None
                    else "Mean positive NEXP: unavailable",
                f"SATUR: {result.maskbit_counts.get('SATUR_'+band.upper(), 0):,} pixels",
                f"MEDIUM: {result.maskbit_counts['MEDIUM']:,}; "
                f"SGA GALAXY: {result.maskbit_counts['GALAXY']:,}",
                "Screening only: not an adopted photometric data product.",
            ]
        elif action == "masks":
            from lsbpipe.surveys.mask_localization import (
                inspect_mask_components, save_mask_components)
            result = inspect_mask_components(
                data_root, field=field, release=hit.release,
                region=hit.region, brick=hit.name, band=band,
                target_radius_arcsec=target_radius_arcsec,
                sky_inner_arcsec=sky_inner_arcsec,
                sky_outer_arcsec=sky_outer_arcsec)
            destination = destination.with_name(destination.name + "_masks.json")
            save_mask_components(result, destination)
            lines = [f"{hit.release.upper()} {hit.region} {hit.name} · mask regions"]
            for key in ("SATUR_"+band.upper(), "MEDIUM", "GALAXY"):
                item = result["bits"][key]
                lines.append(
                    f"{key}: {item['total_pixels_inside_square']:,} pixels, "
                    f"{item['connected_regions']} connected regions")
                if item["regions"]:
                    top = item["regions"][0]
                    lines.append(
                        f"  Largest: {top['pixels']:,} px, "
                        f"{top['angular_separation_arcsec']:.1f}″ from target")
            lines.append("Mask footprints do not identify catalog sources.")
        else:
            from lsbpipe.surveys.qa_plots import render_qa_figure
            destination = destination.with_name(destination.name + "_four-layers.png")
            render_qa_figure(
                directory=data_root, release=hit.release,
                region=hit.region, brick=hit.name, band=band,
                field=field, output=destination,
                target_radius_arcsec=target_radius_arcsec,
                sky_inner_arcsec=sky_inner_arcsec,
                sky_outer_arcsec=sky_outer_arcsec)
            lines = [
                f"{hit.release.upper()} {hit.region} {hit.name} · {band} band",
                "Original IMAGE / INVVAR / NEXP / MASKBITS",
                f"Sky footprint: {field.width_arcmin:g}′ square",
                "Figure uses WCS-aligned local original FITS.",
                "Diagnostic only; no photometric acceptance.",
            ]

    return {
        "action": action,
        "status": "DIAGNOSTIC_NOT_ADOPTED",
        "summary": "\n".join(lines),
        "path": str(destination.resolve()),
        "is_figure": action == "figure",
        "field": {"ra_deg": field.ra_deg, "dec_deg": field.dec_deg,
                  "width_arcmin": field.width_arcmin},
        "provenance": (hit.release, hit.region, hit.name, band),
    }
