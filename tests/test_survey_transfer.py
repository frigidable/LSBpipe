"""No-network download regression tests with mocked HTTP."""
import io
from pathlib import Path
from urllib.error import HTTPError
import pytest

from lsbpipe.surveys.legacy import SkyField, BrickHit, build_manifest
from lsbpipe.surveys.transfer import (
    _validated_product, probe_product, download_product, download_manifest)


def fixture_entry():
    hit = BrickHit('dr9', 'north', '3599p100', 359.8, .1, 10, 10.25,
                   {'g':2, 'r':2, 'z':1})
    return build_manifest(SkyField(359.97,10.1), [hit],
                          kinds=('image',), bands=('g',))['products'][0]


class FakeHTTP(io.BytesIO):
    def __init__(self, payload, *, status=200):
        super().__init__(payload)
        self.status = status
        self.headers = {'Content-Length': str(len(payload))}


def test_head_verification_is_explicit_and_does_not_download(monkeypatch):
    entry = fixture_entry()
    called = []
    def fake_open(request, timeout=None):
        called.append(request.get_method())
        return FakeHTTP(b'')
    monkeypatch.setattr('urllib.request.urlopen', fake_open)
    result = probe_product(entry)
    assert result['evidence'] == 'HTTP_OK'
    assert called == ['HEAD']


def test_404_is_missing_not_a_network_coverage_inference(monkeypatch):
    def missing(request, timeout=None):
        raise HTTPError(request.full_url, 404, 'not found', {}, None)
    monkeypatch.setattr('urllib.request.urlopen', missing)
    assert probe_product(fixture_entry())['evidence'] == 'HTTP_MISSING'


def test_atomic_fits_download_and_local_provenance(monkeypatch, tmp_path):
    payload = b'SIMPLE  =' + b' ' * 79 + b'data'
    monkeypatch.setattr('urllib.request.urlopen',
                        lambda request, timeout=None: FakeHTTP(payload))
    entry = fixture_entry()
    result = download_product(entry, tmp_path)
    path = tmp_path / entry['relative_path']
    assert path.read_bytes() == payload
    assert result['state'] == 'DOWNLOADED'
    assert len(result['sha256']) == 64
    assert not path.with_name(path.name + '.part').exists()
    again = download_product(entry, tmp_path)
    assert again['state'] == 'REUSED_LOCAL_UNVERIFIED'
    assert again['sha256'] == result['sha256']


def test_non_fits_and_malformed_paths_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr('urllib.request.urlopen',
                        lambda request, timeout=None: FakeHTTP(b'<html>not fits</html>'))
    entry = fixture_entry()
    with pytest.raises(IOError, match='not a FITS'):
        download_product(entry, tmp_path)
    assert not (tmp_path / entry['relative_path']).exists()
    assert not list(tmp_path.rglob('*.part'))
    dangerous = dict(entry, relative_path='dr9/../../escape.fits')
    dangerous['url'] = ('https://portal.nersc.gov/cfs/cosmo/data/legacysurvey/'
                        'dr9/../../escape.fits')
    with pytest.raises(ValueError):
        _validated_product(dangerous)


def test_batch_report_and_missing_item_rejected(monkeypatch, tmp_path):
    payload = b'SIMPLE  =' + b' ' * 80
    monkeypatch.setattr('urllib.request.urlopen',
                        lambda request, timeout=None: FakeHTTP(payload))
    entry = fixture_entry()
    manifest = {'schema':'lsbpipe.legacy-download/0.1',
                'field':{'ra_deg':359.97, 'dec_deg':10.1}, 'products':[entry]}
    result = download_manifest(manifest, tmp_path, product_paths={entry['relative_path']})
    assert result['complete']
    assert (tmp_path/'download-report.json').exists()
    with pytest.raises(ValueError, match='not present'):
        download_manifest(manifest, tmp_path, product_paths={'bad.fits'})
