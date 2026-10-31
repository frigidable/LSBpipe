"""Conservative, display-only pixel-quality classes for Legacy coadds.

Quality class 1 marks catalog warnings, including possible target GALAXY:
it does NOT remove them from instrumental validity or any science mask.
"""
import numpy as np
from lsbpipe.survey import LEGACY_BITS, maskbits_to_uint

QUALITY_PALETTE = np.asarray([
    (42, 110, 88),    # 0 instrumentally acceptable, no named warning
    (228, 179, 67),   # 1 BRIGHT/MEDIUM/GALAXY warning, still acceptable
    (220, 72, 67),    # 2 band-specific SATUR conservative exclusion
    (239, 125, 67),   # 3 band-specific ALLMASK conservative exclusion
    (209, 58, 209),   # 4 numerical invalid IMAGE/INVVAR/NEXP
], dtype=np.uint8)


def quality_classes(data: dict, band: str) -> np.ndarray:
    required = ("image", "invvar", "nexp", "maskbits")
    if any(key not in data for key in required):
        raise ValueError("Quality map requires IMAGE, INVVAR, NEXP and MASKBITS")
    if band.lower() not in ("g", "r", "z", "i"):
        raise ValueError("Unsupported quality-map band")
    bits = maskbits_to_uint(data["maskbits"])
    if bits.ndim != 2 or any(np.shape(data[key]) != bits.shape for key in required):
        raise ValueError("Quality inputs must share the same 2D pixel grid")
    classes = np.zeros(bits.shape, dtype=np.uint8)
    warnings = np.uint32(sum(1 << LEGACY_BITS[k]
                             for k in ("BRIGHT", "MEDIUM", "GALAXY")))
    classes[(bits & warnings) != 0] = 1
    classes[(bits & np.uint32(1 << LEGACY_BITS[
        "SATUR_" + band.upper()])) != 0] = 2
    classes[(bits & np.uint32(1 << LEGACY_BITS[
        "ALLMASK_" + band.upper()])) != 0] = 3
    invalid = (~np.isfinite(data["image"]) |
               ~np.isfinite(data["invvar"]) | (np.asarray(data["invvar"]) <= 0) |
               ~np.isfinite(data["nexp"]) | (np.asarray(data["nexp"]) <= 0))
    classes[invalid] = 4
    return classes


def quality_rgb(data: dict, band: str) -> np.ndarray:
    return QUALITY_PALETTE[quality_classes(data, band)]


def quality_summary(data: dict, band: str) -> dict:
    classes = quality_classes(data, band)
    counts = np.bincount(classes.ravel(), minlength=5)
    return {
        "status": "DIAGNOSTIC_NOT_ADOPTED",
        "pixels": int(classes.size),
        "categories": dict(zip(
            ("clean", "catalog_warning_not_excluded", "satur",
             "allmask", "bad_numeric"),
            (int(n) for n in counts),
        )),
    }
