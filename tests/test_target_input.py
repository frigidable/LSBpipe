"""Offline parsing invariants for pasteable survey target fields."""
import pytest

from lsbpipe.gui.target_input import (
    parse_ra_deg, parse_dec_deg, parse_width_arcmin, parse_coordinate_pair,
)


@pytest.mark.parametrize("text,expected", [
    ("154.7709", 154.7709),
    ("154,7709", 154.7709),
    ("154.7709 deg", 154.7709),
    ("10:19:05", (10 + 19 / 60 + 5 / 3600) * 15),
    ("10h19m05s", (10 + 19 / 60 + 5 / 3600) * 15),
    ("23:59:59.9", (23 + 59 / 60 + 59.9 / 3600) * 15),
    ("0", 0),
])
def test_ra_decimal_and_hour_angle(text, expected):
    assert parse_ra_deg(text) == pytest.approx(expected)


@pytest.mark.parametrize("text,expected", [
    ("+46.4537", 46.4537),
    ("46,4537°", 46.4537),
    ("+46:27:13", 46 + 27 / 60 + 13 / 3600),
    ("-46d27m13s", -(46 + 27 / 60 + 13 / 3600)),
    ("\u221246:27:13", -(46 + 27 / 60 + 13 / 3600)),
    ("-0:30:00", -0.5),
    ("-90", -90),
])
def test_dec_decimal_and_dms(text, expected):
    assert parse_dec_deg(text) == pytest.approx(expected)


@pytest.mark.parametrize("text,expected", [
    ("15", 15.0),
    ("15 arcmin", 15.0),
    ("15'", 15.0),
    ("15\u2032", 15.0),
    ("0.25 deg", 15.0),
    ("0,25\u00b0", 15.0),
    ("900 arcsec", 15.0),
    ("900\u2033", 15.0),
    ("120", 120.0),
])
def test_width_angular_units(text, expected):
    assert parse_width_arcmin(text) == pytest.approx(expected)


@pytest.mark.parametrize("text,expected", [
    ("154.7709 46.4537", (154.7709, 46.4537)),
    ("154.7709,46.4537", (154.7709, 46.4537)),
    ("154.7709, 46.4537", (154.7709, 46.4537)),
    ("154,7709 46,4537", (154.7709, 46.4537)),
    ("154.7709\n46.4537", (154.7709, 46.4537)),
    ("10:19:05\t+46:27:13",
     ((10 + 19 / 60 + 5 / 3600) * 15, 46 + 27 / 60 + 13 / 3600)),
])
def test_pair_paste(text, expected):
    ra, dec = parse_coordinate_pair(text)
    assert (ra, dec) == pytest.approx(expected)


@pytest.mark.parametrize("parse,text", [
    (parse_ra_deg, ""),
    (parse_ra_deg, "360"),
    (parse_ra_deg, "-1"),
    (parse_ra_deg, "24:00:00"),
    (parse_ra_deg, "12:61:00"),
    (parse_ra_deg, "12:59:60"),
    (parse_ra_deg, "nan"),
    (parse_ra_deg, "inf"),
    (parse_dec_deg, "91"),
    (parse_dec_deg, "+90:00:01"),
    (parse_dec_deg, "+15:99:00"),
    (parse_dec_deg, "nan"),
    (parse_width_arcmin, "0"),
    (parse_width_arcmin, "-1"),
    (parse_width_arcmin, "121"),
    (parse_width_arcmin, "2.1 deg"),
    (parse_width_arcmin, "7201 arcsec"),
    (parse_width_arcmin, "15 by 15"),
    (parse_coordinate_pair, ""),
    (parse_coordinate_pair, "154.7709"),
    (parse_coordinate_pair, "154.77 46.45 15"),
    (parse_coordinate_pair, "154.77 91"),
])
def test_ambiguous_or_invalid_values_are_rejected(parse, text):
    with pytest.raises(ValueError):
        parse(text)
