"""Pure startup-contract checks for the optional Aladin WebEngine shell.

These tests are deliberately offline and do NOT claim that WebGL2, CDN
scripts or WASM load successfully in an actual browser.
"""
from html.parser import HTMLParser
import pytest

pytest.importorskip("PySide6")
from lsbpipe.gui.survey_explorer import _ALADIN_HTML, _AladinPageHandler


class Scripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts = []
    def handle_starttag(self, tag, attrs):
        if tag == 'script':
            self.scripts.append(dict(attrs))


def test_bootstrap_is_inline_and_does_not_block_on_jquery():
    parser = Scripts()
    parser.feed(_ALADIN_HTML)
    assert len(parser.scripts) == 1
    assert 'src' not in parser.scripts[0]
    assert 'jquery' not in _ALADIN_HTML.lower()
    assert "script.async = true" in _ALADIN_HTML
    assert 'script.onerror' in _ALADIN_HTML
    assert 'script.onload' in _ALADIN_HTML


def test_sky_bridge_and_wasm_diagnostics_contract():
    assert 'window.lsbGo = function' in _ALADIN_HTML
    assert 'window.lsbMapCentre = function' in _ALADIN_HTML
    assert 'window.lsbBricks = function' in _ALADIN_HTML
    assert 'window.lsbDiagnostics = function' in _ALADIN_HTML
    assert "getContext('webgl2')" in _ALADIN_HTML
    assert 'wasm_error' in _ALADIN_HTML
    assert 'script_error' in _ALADIN_HTML
    assert 'timeout' in _ALADIN_HTML


def test_http_handler_has_no_general_directory_access():
    assert _AladinPageHandler.do_GET.__doc__ is None
    assert "if self.path != '/':" in __import__('inspect').getsource(
        _AladinPageHandler.do_GET)
