"""Portable, object-centric research projects and a user-local project library.

The original FITS, legacy local-project metadata and immutable QA runs are
never overwritten by this module. All operations are offline and atomic.
"""
from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import uuid

from .legacy import SkyField
from .local_project import (
    _atomic_json, open_local_project, session_pointer_path,
)

SCHEMA = "lsbpipe.object-project/1"
REGISTRY_SCHEMA = "lsbpipe.project-registry/1"
PROJECT_FILENAME = "lsbpipe-object.json"
REGISTRY_FILENAME = "projects.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def library_index_path() -> Path:
    """Dedicated registry; does not replace the legacy last-project pointer."""
    return session_pointer_path().parent / REGISTRY_FILENAME


def _read_json(path: Path) -> dict:
    try:
        item = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise ValueError(f"Cannot read project metadata at {path}: {exc}") from exc
    if not isinstance(item, dict):
        raise ValueError(f"Project metadata must be a JSON object: {path}")
    return item


def _field_from_payload(data: object) -> SkyField | None:
    if data is None:
        return None
    if not isinstance(data, dict):
        raise ValueError("Invalid stored ICRS coordinates")
    return SkyField(float(data["ra_deg"]), float(data["dec_deg"]),
                    float(data["width_arcmin"]))


def _field_dict(value: SkyField | None) -> dict | None:
    if value is None:
        return None
    return {"ra_deg": value.ra_deg, "dec_deg": value.dec_deg,
            "width_arcmin": value.width_arcmin}


def _project_id(raw: str) -> str:
    try:
        result = uuid.UUID(raw)
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("Invalid immutable project UUID") from exc
    if result.version != 4:
        raise ValueError("Project ID must be UUIDv4")
    return str(result)


@dataclass
class ObjectProject:
    project_id: str
    display_name: str
    root: Path
    field: SkyField | None = None
    aliases: list[str] = dc_field(default_factory=list)
    selected_dataset: tuple[str, str, str, str] | None = None
    data_root: Path | None = None
    target_source: str = "user"
    created_at: str = dc_field(default_factory=_now)
    last_opened_at: str = dc_field(default_factory=_now)
    last_science: dict = dc_field(default_factory=dict)
    catalog_match: dict | None = None

    @property
    def status(self) -> str:
        if self.field is None:
            return "NEEDS_TARGET_POSITION"
        if self.data_root is None:
            return "TARGET_DEFINED_NO_FITS"
        return "LOCAL_DATA_NOT_SCIENTIFICALLY_ADOPTED"


def _validate(project: ObjectProject) -> None:
    project.project_id = _project_id(project.project_id)
    if not isinstance(project.display_name, str) or not project.display_name.strip():
        raise ValueError("Project name cannot be empty")
    if len(project.display_name) > 140 or any(
            ord(c) < 32 or c in r'\/:*?"<>|' for c in project.display_name):
        raise ValueError("Project name contains unsupported characters")
    project.display_name = project.display_name.strip()
    if project.selected_dataset is not None and (
            len(project.selected_dataset) != 4 or
            any(not isinstance(x, str) for x in project.selected_dataset)):
        raise ValueError("Invalid selected survey dataset")
    if project.catalog_match is not None and not isinstance(
            project.catalog_match, dict):
        raise ValueError("Invalid external catalog association")


def project_path(root: str | Path) -> Path:
    return Path(root).expanduser().resolve() / PROJECT_FILENAME


def save_object_project(project: ObjectProject) -> Path:
    """Persist only the new companion metadata; legacy 0.1 stays untouched."""
    _validate(project)
    project.root = project.root.expanduser().resolve()
    if not project.root.is_dir():
        raise FileNotFoundError(f"Project directory missing: {project.root}")
    path = project_path(project.root)
    if path.exists():
        current = _read_json(path)
        if current.get("schema") != SCHEMA or _project_id(
                current.get("project_id")) != project.project_id:
            raise ValueError("Refusing to overwrite a different project identity")
    root = project.root
    # Store data_root relative only if within project root. External paths
    # remain absolute and must be relinked if moved.
    data_root = project.data_root.resolve() if project.data_root else None
    if data_root is None:
        data_link = None
    elif data_root.is_relative_to(root):
        data_link = {"relative": data_root.relative_to(root).as_posix()}
    else:
        data_link = {"absolute": str(data_root)}
    _atomic_json(path, {
        "schema": SCHEMA,
        "project_id": project.project_id,
        "display_name": project.display_name,
        "aliases": list(project.aliases),
        "field": _field_dict(project.field),
        "target_source": project.target_source,
        "selected_dataset": (
            list(project.selected_dataset) if project.selected_dataset else None),
        "data_root": data_link,
        "created_at": project.created_at,
        "last_opened_at": project.last_opened_at,
        "last_science": project.last_science,
        "catalog_match": project.catalog_match,
        "status": project.status,
        "science_status": "DIAGNOSTIC_NOT_ADOPTED",
    })
    return path


def read_object_project(root: str | Path) -> ObjectProject:
    folder = Path(root).expanduser().resolve()
    path = project_path(folder)
    info = _read_json(path)
    if info.get("schema") != SCHEMA:
        raise ValueError(f"Unsupported project schema in {path}")
    link = info.get("data_root")
    if link is None:
        data_root = None
    elif isinstance(link, dict) and isinstance(link.get("relative"), str):
        value = Path(link["relative"])
        if value.is_absolute() or ".." in value.parts or not value.parts:
            raise ValueError("Unsafe relative data directory in project manifest")
        data_root = folder / value
    elif isinstance(link, dict) and isinstance(link.get("absolute"), str):
        data_root = Path(link["absolute"])
        if not data_root.is_absolute():
            raise ValueError("External data directory must be absolute")
    else:
        raise ValueError("Invalid data location in project manifest")
    selected = info.get("selected_dataset")
    project = ObjectProject(
        project_id=_project_id(info.get("project_id")),
        display_name=info.get("display_name"), root=folder,
        field=_field_from_payload(info.get("field")),
        aliases=list(info.get("aliases") or []),
        selected_dataset=tuple(selected) if selected else None,
        data_root=data_root, target_source=str(info.get("target_source", "user")),
        created_at=str(info.get("created_at", "")),
        last_opened_at=str(info.get("last_opened_at", "")),
        last_science=info.get("last_science") or {},
        catalog_match=info.get("catalog_match"))
    _validate(project)
    return project


def _registry_rows() -> list[dict]:
    path = library_index_path()
    if not path.is_file():
        return []
    raw = _read_json(path)
    if raw.get("schema") != REGISTRY_SCHEMA or not isinstance(
            raw.get("projects"), list):
        raise ValueError("Unrecognized project registry schema; no overwrite")
    return raw["projects"]


def recent_projects() -> list[dict]:
    """Preserve missing-directory entries for explicit relinking in the hub."""
    rows = _registry_rows()
    return sorted(
        ({**r, "available": project_path(r["root"]).is_file()}
         for r in rows if isinstance(r, dict) and
         isinstance(r.get("root"), str) and isinstance(r.get("project_id"), str)),
        key=lambda x: x.get("last_opened_at", ""), reverse=True)


def register_object_project(project: ObjectProject) -> None:
    _validate(project)
    rows = _registry_rows()
    canonical = str(project.root.resolve())
    updated = [x for x in rows if x.get("project_id") != project.project_id and
               Path(x.get("root", "")).resolve() != project.root.resolve()]
    updated.append({
        "project_id": project.project_id, "display_name": project.display_name,
        "root": canonical, "last_opened_at": project.last_opened_at})
    _atomic_json(library_index_path(), {
        "schema": REGISTRY_SCHEMA, "projects": updated})


def touch_object_project(project: ObjectProject) -> None:
    project.last_opened_at = _now()
    save_object_project(project)
    register_object_project(project)


def create_object_project(name: str, directory: str | Path, *,
                          field: SkyField | None = None) -> ObjectProject:
    root = Path(directory).expanduser().resolve()
    if root.exists() and any(root.iterdir()):
        raise FileExistsError(
            "Project folder is not empty; use Import existing project instead")
    if any(item["display_name"].casefold() == name.strip().casefold()
           and item.get("available") for item in recent_projects()):
        raise ValueError("A project with this name is already in the library")
    root.mkdir(parents=True, exist_ok=True)
    project = ObjectProject(
        project_id=str(uuid.uuid4()), display_name=name, root=root,
        field=field)
    save_object_project(project)
    register_object_project(project)
    return project


def import_object_project(directory: str | Path, *,
                          fallback_field: SkyField | None = None,
                          display_name: str | None = None) -> ObjectProject:
    """Adopt old 0.1/download-report files without mutating either."""
    root = Path(directory).expanduser().resolve()
    if (root / PROJECT_FILENAME).is_file():
        project = read_object_project(root)
        touch_object_project(project)
        return project
    prior = open_local_project(root, fallback_field=fallback_field)
    # Name is user-editable. Legacy imports use the folder name only as
    # a descriptive default, never as the official galaxy identity.
    project = ObjectProject(
        project_id=str(uuid.uuid4()),
        display_name=display_name or prior.root.name, root=prior.root,
        data_root=prior.data_root, field=prior.field,
        target_source=prior.field_source, selected_dataset=prior.selected,
        last_science=prior.last_science)
    save_object_project(project)
    register_object_project(project)
    return project


def relink_object_project(project_id: str, new_directory: str | Path) -> ObjectProject:
    """Change only the library location after verifying the project's UUID."""
    path = Path(new_directory).expanduser().resolve()
    candidate = read_object_project(path)
    if candidate.project_id != _project_id(project_id):
        raise ValueError("Relink refused: selected directory is another project")
    register_object_project(candidate)
    return candidate
