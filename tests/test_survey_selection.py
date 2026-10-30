"""Offline deterministic tests for safe survey download selection."""
import pytest
from lsbpipe.surveys.selection import matching_product, summarize_selection


PRODUCTS = [
    {"relative_path": "a", "brick": "1000p100", "kind": "image",
     "band": "g", "evidence": "HTTP_OK", "size_bytes": 100},
    {"relative_path": "b", "brick": "1000p100", "kind": "maskbits",
     "band": None, "evidence": "UNVERIFIED_URL", "size_bytes": None},
    {"relative_path": "c", "brick": "1001p100", "kind": "image",
     "band": "r", "evidence": "HTTP_MISSING", "size_bytes": None},
    {"relative_path": "d", "brick": "1001p100", "kind": "tractor",
     "band": None, "evidence": "HTTP_OK", "size_bytes": 400},
]


def test_brick_filters_are_exact_and_do_not_change_selection():
    assert [x["relative_path"] for x in PRODUCTS if
            matching_product(x, brick="1000p100")] == ["a", "b"]
    assert [x["relative_path"] for x in PRODUCTS if
            matching_product(x, kind="image")] == ["a", "c"]
    assert matching_product(PRODUCTS[0], band="g")
    assert not matching_product(PRODUCTS[0], band="r")


def test_band_selection_keeps_shared_maskbits_and_tractor():
    assert [x["relative_path"] for x in PRODUCTS if
            matching_product(x, band="g")] == ["a", "b", "d"]
    assert [x["relative_path"] for x in PRODUCTS if
            matching_product(x, band="z", kind="image")] == []


def test_download_summary_never_treats_unknown_as_zero_total():
    report = summarize_selection(PRODUCTS, {"a", "b", "c"})
    assert report.selected_files == 3
    assert report.verified_files == 1
    assert report.missing_files == 1
    assert report.unknown_files == 1
    assert report.known_bytes == 100
    assert report.unknown_size_files == 2
    assert not report.can_claim_total_size


def test_complete_size_requires_every_selected_http_ok():
    report = summarize_selection(PRODUCTS, {"a", "d"})
    assert report.known_bytes == 500
    assert report.unknown_size_files == 0
    assert report.can_claim_total_size
    assert report.known_mib == pytest.approx(500 / (1024**2))


def test_unknown_selection_is_rejected_instead_of_silently_ignored():
    with pytest.raises(ValueError, match="absent"):
        summarize_selection(PRODUCTS, {"unknown"})
    with pytest.raises(ValueError, match="Duplicated"):
        summarize_selection(PRODUCTS + [PRODUCTS[0]], {"a"})


def test_only_http_ok_may_contribute_to_known_bytes():
    product = dict(PRODUCTS[2], size_bytes=99999999)
    report = summarize_selection([product], {"c"})
    assert report.known_bytes == 0
    assert report.unknown_size_files == 1
