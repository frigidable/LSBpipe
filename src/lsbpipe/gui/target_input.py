"""Strict, locale-friendly target entry for the LSBpipe GUI.

Parsing is independent of Qt and network access. RA decimal input is in
DEGREES; colon/hms RA is in hours. Field width is always returned in
arcminutes. Ambiguous or out-of-range inputs are rejected, never clamped.
"""
from __future__ import annotations

import math
import re

_NUMBER = r"[+-]?(?:\d+(?:[.,]\d*)?|[.,]\d+)"
_PAIR_SPLIT = re.compile(r"\s*[;\n\t]\s*")


def _number(raw: str, label: str) -> float:
    value = raw.strip().replace("\u2212", "-")
    if not re.fullmatch(_NUMBER, value):
        raise ValueError(f"{label}: enter a finite number")
    number = float(value.replace(",", "."))
    if not math.isfinite(number):
        raise ValueError(f"{label}: number must be finite")
    return number


def _sexagesimal(raw: str, *, ra: bool) -> float | None:
    normalized = raw.strip().lower().replace("\u2212", "-")
    # "10h19m05.016s" / "+46d27m13.3s" / "10:19:05"
    normalized = re.sub(r"\s+", "", normalized)
    if not (":" in normalized or re.search(r"[hmsd]", normalized)):
        return None
    normalized = (normalized.replace("h", ":").replace("d", ":")
                  .replace("m", ":").removesuffix("s"))
    parts = normalized.split(":")
    if len(parts) not in (2, 3) or any(not v for v in parts):
        raise ValueError("Use hh:mm:ss for RA or ±dd:mm:ss for Dec")
    whole = parts[0]
    if not re.fullmatch(r"[+-]?\d{1,3}", whole):
        raise ValueError("Invalid sexagesimal degrees/hours")
    if not re.fullmatch(r"\d{1,2}", parts[1]):
        raise ValueError("Minutes must be an integer 0–59")
    seconds = _number(parts[2], "Seconds") if len(parts) == 3 else 0.
    minutes = int(parts[1])
    if minutes >= 60 or not 0 <= seconds < 60:
        raise ValueError("Minutes and seconds must be in [0,60)")
    sign = -1 if whole.startswith("-") else 1
    major = abs(int(whole))
    if ra:
        if whole.startswith("-") or major >= 24:
            raise ValueError("Hour-angle RA must be 00h–23h")
        return (major + minutes / 60 + seconds / 3600) * 15.
    if major > 90 or (major == 90 and (minutes or seconds)):
        raise ValueError("Declination must be between −90° and +90°")
    return sign * (major + minutes / 60 + seconds / 3600)


def parse_ra_deg(raw: str) -> float:
    """Decimal degrees (0 <= RA < 360) or sexagesimal hour angle."""
    cleaned = raw.strip().replace("\u00b0", "").strip()
    cleaned = re.sub(r"\s*(deg|degrees)\s*$", "", cleaned, flags=re.I)
    value = _sexagesimal(cleaned, ra=True)
    if value is None:
        value = _number(cleaned, "RA")
    if not 0 <= value < 360:
        raise ValueError("RA must be from 0° inclusive to 360° exclusive")
    return value


def parse_dec_deg(raw: str) -> float:
    """Signed decimal degrees or sexagesimal ±dd:mm:ss."""
    cleaned = raw.strip().replace("\u00b0", "").strip()
    cleaned = re.sub(r"\s*(deg|degrees)\s*$", "", cleaned, flags=re.I)
    value = _sexagesimal(cleaned, ra=False)
    if value is None:
        value = _number(cleaned, "Dec")
    if not -90 <= value <= 90:
        raise ValueError("Dec must be in [−90°, +90°]")
    return value


def parse_width_arcmin(raw: str) -> float:
    """Square search field width, 0 < arcmin <= 120.

    Default unit: arcmin. Supported: arcmin, ', ′, deg, °, arcsec, ", ″.
    """
    text = raw.strip().lower().replace("\u2212", "-")
    m = re.fullmatch(
        rf"\s*({_NUMBER})\s*(arcminutes?|arcmins?|amin|arcmin|"
        rf"arcseconds?|arcsecs?|asec|arcsec|degrees?|deg|"
        rf"\u00b0|\u2032|'|\u2033|\"|)?\s*", text
    )
    if not m:
        raise ValueError("Width: use e.g. 15, 15′, 0.25 deg or 900″")
    value = _number(m.group(1), "Width")
    unit = m.group(2) or "arcmin"
    if unit in ("deg", "degree", "degrees", "\u00b0"):
        value *= 60
    elif unit in ("arcsec", "arcsecs", "arcsecond", "arcseconds",
                  "asec", "\u2033", '"'):
        value /= 60
    if not 0 < value <= 120:
        raise ValueError("Field width must be > 0 and ≤ 120 arcmin")
    return value


def parse_coordinate_pair(raw: str) -> tuple[float, float]:
    """Paste a two-component coordinate pair; fail on ambiguous tokenization.

    Supported: "154.77 46.45", "154.77,46.45", "154,77 46,45",
    "10:19:05 +46:27:13", two lines, tab or semicolon separated.
    """
    text = raw.strip()
    if not text:
        raise ValueError("Clipboard is empty")
    if _PAIR_SPLIT.search(text):
        parts = _PAIR_SPLIT.split(text)
    elif re.search(r",\s+", text):
        parts = re.split(r",\s+", text)
    else:
        parts = text.split()
        if len(parts) != 2 and text.count(",") == 1:
            parts = text.split(",")
    if len(parts) != 2:
        raise ValueError(
            "Paste exactly two values: RA Dec, separated by space, comma, "
            "semicolon, tab or newline")
    return parse_ra_deg(parts[0]), parse_dec_deg(parts[1])
