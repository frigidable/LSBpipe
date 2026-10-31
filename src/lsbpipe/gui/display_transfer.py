"""Physically labeled, display-only transfer functions for scientific FITS.

This module never changes source pixel values or science validity masks.
Limits are in the ORIGINAL layer units; logarithmic display of an IMAGE
requires positive samples and deliberately does not shift sky-subtracted
negative fluxes by an arbitrary offset.
"""
from __future__ import annotations

import math
import numpy as np


def transfer_settings(kind: str, contrast: str = "standard",
                      scaling: str = "auto",
                      limits: tuple[float, float] | None = None):
    if kind not in ("image", "invvar", "nexp"):
        raise ValueError("Manual transfer is available only for scalar FITS layers")
    if contrast not in ("standard", "faint"):
        raise ValueError("Contrast must be standard or faint")
    permitted = {
        "image": ("linear", "asinh", "log"),
        "invvar": ("linear", "log"),
        "nexp": ("linear",),
    }[kind]
    resolved = ({"image": "asinh", "invvar": "log",
                 "nexp": "linear"}[kind] if scaling == "auto" else scaling)
    if resolved not in permitted:
        raise ValueError(f"Unsupported {kind} display transfer: {scaling}")
    if limits is not None:
        if len(limits) != 2:
            raise ValueError("Display limits must be (vmin, vmax)")
        lo, hi = float(limits[0]), float(limits[1])
        if not (math.isfinite(lo) and math.isfinite(hi) and lo < hi):
            raise ValueError("Display limits must be finite with vmin < vmax")
        if resolved == "log" and lo <= 0:
            raise ValueError("Logarithmic display requires vmin > 0")
        if kind == "nexp" and lo < 0:
            raise ValueError("NEXP display limits must not be negative")
        return resolved, (lo, hi)
    return resolved, None


def display_range(data: np.ndarray, kind: str, *,
                  contrast: str = "standard", scaling: str = "auto",
                  limits: tuple[float, float] | None = None) -> tuple[float, float, str]:
    mode, manual = transfer_settings(kind, contrast, scaling, limits)
    if np.ndim(data) != 2:
        raise ValueError("FITS display requires a 2D layer")
    if manual is not None:
        return manual[0], manual[1], mode
    # A fixed stride aliases regular detector/stripe patterns (e.g. every
    # fourth pixel), potentially omitting an entire flux population.
    # Deterministic uniform pixel sampling avoids periodic stride bias while
    # bounding temporary data to 250k samples, even for large FITS arrays.
    array = np.asarray(data)
    if array.size <= 250_000:
        sample = np.asarray(array, dtype=np.float64).ravel()
    else:
        indices = np.random.default_rng(1909).integers(
            0, array.size, size=250_000)
        sample = np.asarray(array.flat[indices], dtype=np.float64)
    finite = np.isfinite(sample)
    if kind == "invvar" or mode == "log":
        finite &= sample > 0
    if kind == "nexp":
        finite &= sample >= 0
    if not np.any(finite):
        # A positive placeholder is required for the display-only log
        # colorbar. No source pixels are fabricated or made valid.
        return (1e-3, 1., mode) if mode == "log" else (0., 1., mode)
    sample = sample[finite]
    if kind == "nexp":
        return 0., max(float(np.max(sample)), 1.), mode
    percentiles = ((2., 98.5) if contrast == "faint" else
                   (1., 99.7)) if kind == "image" else (3., 97.)
    lo, hi = (float(v) for v in np.percentile(sample, percentiles))
    if mode == "log":
        lo = max(lo, float(np.min(sample)))
    if hi <= lo:
        if mode == "log":
            return max(lo / 10., 1e-300), max(lo, 1e-299), mode
        return lo - 0.5, lo + 0.5, mode
    return lo, hi, mode


def display_gray(data: np.ndarray, kind: str, *,
                 contrast: str = "standard", scaling: str = "auto",
                 limits: tuple[float, float] | None = None) -> np.ndarray:
    lo, hi, mode = display_range(
        data, kind, contrast=contrast, scaling=scaling, limits=limits)
    arr = np.asarray(data)
    valid = np.isfinite(arr)
    if kind == "invvar" or mode == "log":
        valid &= arr > 0
    if kind == "nexp":
        valid &= arr >= 0
    result = np.zeros(arr.shape, dtype=np.uint8)
    if not np.any(valid):
        return result
    # Only valid numerical samples get a transformed value.
    # Zero/infinite/no-data are marked separately in FITS overlays.
    selected = np.asarray(arr[valid], dtype=np.float64)
    if mode == "log":
        x = (np.log10(np.maximum(selected, lo)) - math.log10(lo)) / (
            math.log10(hi) - math.log10(lo))
    else:
        x = (selected - lo) / (hi - lo)
    x = np.clip(x, 0., 1.)
    if mode == "asinh":
        strength = 24. if contrast == "faint" else 7.
        x = np.arcsinh(strength * x) / math.asinh(strength)
    result[valid] = np.rint(x * 255.).astype(np.uint8)
    return result
