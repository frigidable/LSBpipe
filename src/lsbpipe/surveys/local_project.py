"""Persistent, offline-first local Legacy FITS project sessions.

This module indexes only local file paths, not astronomical measurements. It
never mutates original science FITS, uses network services or assumes a file
is scientifically valid merely because it is present on disk.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field as dc_field
import json
import os
from pathlib import Path
import re
import tempfile

from .legacy import BrickHit, SkyField, product_for

PROJECT_FILE = "lsbpipe-project.json"
SCHEMA = "lsbpipe.local-project/0.1"
IMAGE_RE = re.compile(
    r"^legacysurvey-(\d{4}[pm]\d{3})-image-([griz])\.fits\.fz$")
REGIONS = {"dr9": ("north", "south"), "dr10": ("south",),
           "dr11": ("north", "south")}


def _atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".lsbpipe-", suffix=".tmp",
                                dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2,
                      allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def session_pointer_path() -> Path:
    override = os.environ.get("LSBPIPE_SESSION_DIR")
    if override:
        return Path(override).expanduser() / "last-project.json"
    appdata = os.environ.get("LOCALAPPDATA")
    return ((Path(appdata) / "LSBpipe" if appdata else
             Path.home() / ".config" / "lsbpipe") / "last-project.json")


def last_project_directory() -> Path | None:
    path = session_pointer_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("schema") != SCHEMA or not isinstance(data.get("root"), str):
            return None
        root = Path(data["root"]).expanduser()
        return root if root.is_dir() else None
    except (OSError, ValueError, TypeError):
        return None


def remember_project_directory(root: Path) -> None:
    _atomic_json(session_pointer_path(), {
        "schema": SCHEMA, "root": str(Path(root).resolve())})


def _read_metadata(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Unreadable project metadata: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Project metadata must contain a JSON object: {path}")
    return data


def _data_roots(path: Path):
    return (path, path / "data")


def _candidate_images(data_root: Path):
    for release, regions in REGIONS.items():
        for region in regions:
            directory = data_root / release / region / "coadd"
            if not directory.is_dir():
                continue
            for image in sorted(directory.glob("*/*/legacysurvey-*-image-*.fits.fz")):
                match = IMAGE_RE.fullmatch(image.name)
                if not match:
                    continue
                brick, band = match.groups()
                if image.parent.name != brick or image.parent.parent.name != brick[:3]:
                    continue
                yield release, region, brick, band


@dataclass
class LocalProject:
    root: Path
    data_root: Path
    field: SkyField
    hits: list[BrickHit]
    complete: dict[tuple[str, str, str, str], bool]
    on_disk: set[str]
    selected: tuple[str, str, str, str] | None = None
    target_radius_arcsec: float = 60.
    sky_inner_arcsec: float = 90.
    sky_outer_arcsec: float = 180.
    last_science: dict = dc_field(default_factory=dict)
    field_source: str = ""


def open_local_project(directory: str | Path, *,
                       fallback_field: SkyField | None = None) -> LocalProject:
    """Recover survey provenance from original paths, not from network/catalog.

    A download-report or project.json gives target coordinates. Without either,
    caller must provide explicit current ICRS coordinates; brick name alone
    does not determine a galaxy center.
    """
    root = Path(directory).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Local project folder not found: {root}")

    alternatives = []
    for data_root in _data_roots(root):
        found = list(dict.fromkeys(_candidate_images(data_root)))
        if found:
            alternatives.append((data_root, found))
    if not alternatives:
        raise FileNotFoundError(
            "No original Legacy FITS IMAGE files under dr*/<region>/coadd/; "
            "choose the original download root or its parent containing data/.")
    data_root, items = max(alternatives, key=lambda x: len(x[1]))
    project_root = data_root.parent if data_root.name == "data" else data_root
    metadata = _read_metadata(project_root / PROJECT_FILE)
    if metadata is not None and metadata.get("schema") != SCHEMA:
        raise ValueError(f"Unknown {PROJECT_FILE} schema; not overwriting it")
    report = _read_metadata(data_root / "download-report.json")
    if report is None and project_root != data_root:
        report = _read_metadata(project_root / "download-report.json")
    if report is not None and report.get("schema") != "lsbpipe.legacy-download-report/0.1":
        raise ValueError("Unexpected original download-report schema")

    field_payload = ((metadata or {}).get("field") or
                     (report or {}).get("field"))
    if field_payload is None:
        if fallback_field is None:
            raise ValueError(
                "No saved target coordinates. Enter the actual ICRS RA/Dec "
                "and field width in the GUI, then choose Open local project.")
        field = fallback_field
        source = "explicit_gui_target"
    else:
        if not isinstance(field_payload, dict):
            raise ValueError("Invalid ICRS field in project metadata")
        field = SkyField(float(field_payload["ra_deg"]),
                         float(field_payload["dec_deg"]),
                         float(field_payload["width_arcmin"]))
        source = ("project" if metadata and metadata.get("field") else "download-report")

    discovered = {}
    complete = {}
    on_disk = set()
    for release, region, brick, band in items:
        # Geometry and median NEXP are not recoverable from a filename.
        # The GUI must not render these placeholder bounds as survey bricks.
        hit = BrickHit(release, region, brick,
                       field.ra_deg, field.ra_deg,
                       field.dec_deg, field.dec_deg, {})
        group = (release, region, brick)
        discovered.setdefault(group, set()).add(band)
        all_present = True
        for kind in ("image", "invvar", "nexp", "maskbits"):
            product = product_for(
                BrickHit(release, region, brick, 0, 0, 0, 0, {band: None}),
                kind, None if kind == "maskbits" else band)
            file_path = data_root / product.relative_path
            if (file_path.is_file() and file_path.stat().st_size > 0):
                on_disk.add(product.relative_path)
            else:
                all_present = False
        complete[(release, region, brick, band)] = all_present

    hits = [
        BrickHit(release, region, brick,
                 field.ra_deg, field.ra_deg, field.dec_deg, field.dec_deg,
                 {band: None for band in sorted(bands)})
        for (release, region, brick), bands in sorted(discovered.items())
    ]
    saved = metadata or {}
    selected = None
    value = saved.get("selected")
    if isinstance(value, list) and len(value) == 4:
        key = tuple(value)
        if key in complete:
            selected = key
    if selected is None:
        # Prefer a complete local r-band quartet when there is a choice.
        selected = next((k for k, good in complete.items()
                         if good and k[3] == "r"), None)
        if selected is None:
            selected = next((k for k, good in complete.items() if good), None)
        if selected is None:
            selected = next(iter(complete), None)

    def radius(key, default):
        try:
            return float(saved.get(key, default))
        except (TypeError, ValueError):
            return default

    target = radius("target_radius_arcsec", 60.)
    inner = radius("sky_inner_arcsec", 90.)
    outer = radius("sky_outer_arcsec", 180.)
    if not (0 < target <= inner < outer <= 1200):
        target, inner, outer = 60., 90., 180.
    last_science = saved.get("last_science", {})
    if not isinstance(last_science, dict):
        last_science = {}
    if not last_science and selected is not None:
        # Import older QA runs created before local-project persistence existed.
        # Use only verified *metadata identity* for navigation, never claim
        # these historic artifacts' original-FITS hashes were revalidated.
        qa_dir = data_root.parent / "qa-runs"
        artifact_names = {
            "square": "square.json", "census": "mask-census.json",
            "masks": "mask-components.json", "figure": "four-layers.png",
            "compare": "north-south.json",
        }
        if qa_dir.is_dir():
            for folder in sorted(qa_dir.iterdir(), reverse=True):
                if not folder.is_dir() or folder.name.startswith("."):
                    continue
                receipt_path = folder / "manifest.json"
                if not receipt_path.is_file():
                    continue
                try:
                    receipt = _read_metadata(receipt_path)
                except ValueError:
                    continue
                if receipt.get("schema") != "lsbpipe.gui-science-qa/1":
                    continue
                context = receipt.get("context", {})
                action = context.get("action")
                artifact = artifact_names.get(action)
                if not artifact or not (folder / artifact).is_file():
                    continue
                if not all(context.get(k) == v for k, v in (
                        ("release", selected[0]), ("region", selected[1]),
                        ("brick", selected[2]), ("band", selected[3]))):
                    continue
                if not all(abs(float(context.get(k, -999)) - v) < 1e-9
                           for k, v in (
                               ("ra_deg", field.ra_deg),
                               ("dec_deg", field.dec_deg),
                               ("width_arcmin", field.width_arcmin))):
                    continue
                last_science = {
                    "action": action, "path": str(folder / artifact),
                    "manifest_path": str(receipt_path),
                    "summary": "Previously saved QA artifact (input hashes not "
                               "revalidated on reopen)",
                    "provenance": list(selected),
                }
                if metadata is None:
                    # Pre-session QA receipts also retain the exact screening
                    # radii. Restore them only when their ordering is valid.
                    try:
                        t = float(context["target_radius_arcsec"])
                        i = float(context["sky_inner_arcsec"])
                        o = float(context["sky_outer_arcsec"])
                        if 0 < t <= i < o <= 1200:
                            target, inner, outer = t, i, o
                    except (KeyError, ValueError, TypeError):
                        pass
                break
    return LocalProject(
        root=project_root, data_root=data_root, field=field,
        hits=hits, complete=complete, on_disk=on_disk, selected=selected,
        target_radius_arcsec=target, sky_inner_arcsec=inner,
        sky_outer_arcsec=outer, last_science=last_science,
        field_source=source)


def save_local_project(project: LocalProject) -> Path:
    """Atomically persist session metadata *outside* the FITS data tree."""
    path = project.root / PROJECT_FILE
    existing = _read_metadata(path)
    if existing is not None and existing.get("schema") != SCHEMA:
        raise ValueError(f"Refusing to overwrite an unknown {PROJECT_FILE}")
    record = {
        "schema": SCHEMA,
        "field": asdict(project.field),
        "data_root": str(project.data_root),
        "selected": list(project.selected) if project.selected else None,
        "target_radius_arcsec": project.target_radius_arcsec,
        "sky_inner_arcsec": project.sky_inner_arcsec,
        "sky_outer_arcsec": project.sky_outer_arcsec,
        "last_science": project.last_science,
        "status": "LOCAL_FILES_PRESENT_NOT_SCIENTIFICALLY_ADOPTED",
    }
    _atomic_json(path, record)
    remember_project_directory(project.root)
    return path
