"""Offline regression tests: official catalog schema, RA wrap and manifest semantics."""
from dataclasses import asdict
import numpy as np
import pytest

from lsbpipe.surveys.legacy import (
    SkyField, BrickHit, regions_for_release, find_bricks, product_for,
    build_manifest, geometry_url, summary_url)


def _tables():
    geometry = np.array([
        (b'3599p100', 359.80, 0.10, 10.00, 10.25),
        (b'0001p100', 0.10, 0.30, 10.00, 10.25),
        (b'0211p100', 21.00, 21.25, 10.00, 10.25),
    ], dtype=[('BRICKNAME', 'S8'), ('RA1', 'f8'), ('RA2', 'f8'),
              ('DEC1', 'f8'), ('DEC2', 'f8')])
    summary = np.array([
        (b'3599p100', 2, 0, 3),
        (b'0211p100', 3, 1, 0),
    ], dtype=[('BRICKNAME', 'S8'), ('NEXP_G', 'i2'),
              ('NEXP_R', 'i2'), ('NEXP_Z', 'i2')])
    return geometry, summary


def test_ra_zero_intersection_and_footprint_join_not_geometry_only():
    geometry, summary = _tables()
    hits = find_bricks(SkyField(359.97, 10.1, 15),
                       geometry, summary, 'dr9', 'north')
    assert len(hits) == 1
    assert hits[0].name == '3599p100'
    assert hits[0].median_exposures == {'g': 2, 'r': 0, 'z': 3}
    assert hits[0].bands_with_median_exposure == ('g', 'z')
    assert not find_bricks(SkyField(90, 0, 15), geometry, summary,
                           'dr9', 'north')


def test_region_and_band_rules_and_correct_official_urls():
    assert regions_for_release('dr10') == ('south',)
    assert regions_for_release('dr11') == ('north', 'south')
    assert summary_url('dr9', 'north').endswith(
        '/dr9/north/survey-bricks-dr9-north.fits.gz')
    assert geometry_url('dr11').endswith('/dr11/survey-bricks.fits.gz')
    with pytest.raises(ValueError):
        summary_url('dr10', 'north')
    geom, summary = _tables()
    hit = find_bricks(SkyField(359.97, 10.1, 15),
                      geom, summary, 'dr9', 'north')[0]
    image = product_for(hit, 'image', 'g')
    assert image.relative_path == (
        'dr9/north/coadd/359/3599p100/legacysurvey-3599p100-image-g.fits.fz')
    assert image.url.endswith(image.relative_path)
    bits = product_for(hit, 'maskbits')
    assert bits.relative_path.endswith('legacysurvey-3599p100-maskbits.fits.fz')
    tractor = product_for(hit, 'tractor')
    assert tractor.relative_path == 'dr9/north/tractor/359/tractor-3599p100.fits'
    with pytest.raises(ValueError, match='unavailable'):
        product_for(hit, 'image', 'i')


def test_manifest_is_candidate_not_verified_even_for_median_zero():
    geometry, summary = _tables()
    hits = find_bricks(SkyField(359.97, 10.1, 15),
                       geometry, summary, 'dr9', 'north')
    result = build_manifest(SkyField(359.97, 10.1, 15), hits)
    assert result['status'] == 'CANDIDATE_URLS_NOT_VERIFIED'
    assert result['brick_count'] == 1
    assert len(result['products']) == 11  # 3*(image, ivar, nexp) + maskbits + tractor
    assert any(x['band'] == 'r' and x['evidence'] == 'UNVERIFIED_URL'
               for x in result['products'])
    assert len({x['relative_path'] for x in result['products']}) == len(result['products'])
    assert not any(x['band'] == 'i' for x in result['products'])


def test_invalid_field_and_brick_cap():
    for args in ((-1, 0, 15), (0, 91, 15), (20, 0, 0),
                 (40, 0, 121), (float('nan'), 0, 15)):
        with pytest.raises(ValueError):
            SkyField(*args)
    hit = BrickHit('dr9', 'north', '3599p100', 359.8, .1, 10, 10.25,
                   {'g': 1, 'r': 1, 'z': 1})
    with pytest.raises(ValueError, match='max_bricks'):
        build_manifest(SkyField(0, 10), [hit] * 49)
    with pytest.raises(ValueError):
        product_for(BrickHit('dr9', 'north', '../evil', 0, 1, 0, 1, {'g':1}),
                    'image', 'g')


def test_related_northern_releases_preserve_independent_download_provenance():
    hit1 = BrickHit('dr9','north','1544p465',154.4,154.6,46.4,46.6,
                    {'g':3,'r':3,'z':3})
    hit2 = BrickHit('dr11','north','1544p465',154.4,154.6,46.4,46.6,
                    {'g':3,'r':3,'z':3})
    manifest = build_manifest(SkyField(154.5,46.5),[hit1,hit2],
                              kinds=('image',),bands=('r',))
    assert manifest['brick_count'] == 2
    assert manifest['unique_spatial_bricks'] == 1
    assert len(manifest['products']) == 2
    assert {p['release'] for p in manifest['products']} == {'dr9','dr11'}
    assert manifest['related_release_groups'] == [{
        'region':'north','brick':'1544p465','releases':['dr11','dr9'],
        'relation':'SHARED_HISTORICAL_INPUT_OBSERVATIONS_NOT_IDENTICAL_PRODUCTS'
    }] or (
        manifest['related_release_groups'][0]['region'] == 'north' and
        set(manifest['related_release_groups'][0]['releases']) == {'dr9','dr11'})
