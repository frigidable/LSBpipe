"""Pure selection/filter helpers for candidate Legacy science products.

File visibility never changes the selected set. A missing HEAD Content-Length
must not be counted as zero bytes of a known download.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Iterable, Mapping


def matching_product(entry: Mapping, *, brick: str = "All bricks",
                     band: str = "All bands", kind: str = "All products") -> bool:
    """A filter by band retains shared files (Tractor / MASKBITS)."""
    if brick != "All bricks" and entry.get("brick") != brick:
        return False
    if kind != "All products" and entry.get("kind") != kind:
        return False
    if band != "All bands" and entry.get("band") not in (band, None):
        return False
    return True


@dataclass(frozen=True)
class SelectionSummary:
    selected_files: int
    verified_files: int
    missing_files: int
    unknown_files: int
    known_bytes: int
    unknown_size_files: int

    @property
    def known_mib(self) -> float:
        return self.known_bytes / (1024 ** 2)

    @property
    def can_claim_total_size(self) -> bool:
        return self.unknown_size_files == 0


def summarize_selection(products: Iterable[Mapping],
                        selected_paths: set[str]) -> SelectionSummary:
    seen = set()
    selected = 0
    verified = 0
    missing = 0
    unknown = 0
    unknown_size = 0
    total_bytes = 0
    for entry in products:
        path = entry.get("relative_path")
        if path not in selected_paths:
            continue
        if path in seen:
            raise ValueError(f"Duplicated selected product path: {path}")
        seen.add(path)
        selected += 1
        state = entry.get("evidence", "UNVERIFIED_URL")
        if state == "HTTP_OK":
            verified += 1
        elif state == "HTTP_MISSING":
            missing += 1
        else:
            unknown += 1
        size = entry.get("size_bytes")
        if state == "HTTP_OK" and type(size) is int and size >= 0:
            total_bytes += size
        else:
            unknown_size += 1
    if len(seen) != len(selected_paths):
        raise ValueError("Selection contains products absent from current manifest")
    return SelectionSummary(selected, verified, missing, unknown,
                            total_bytes, unknown_size)
