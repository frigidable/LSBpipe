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

def test_diagnostics_serializes_browser_result_and_exposes_raw_status():
    import inspect
    from lsbpipe.gui.survey_explorer import SurveyExplorer, _DiagnosticWebPage
    diagnostics = inspect.getsource(SurveyExplorer.show_map_diagnostics)
    assert 'JSON.stringify' in diagnostics
    assert 'documentReady' in diagnostics
    assert 'onPageNotice' in diagnostics
    assert 'recent_console' in diagnostics
    assert 'json.loads(payload)' in diagnostics
    assert 'JavaScript returned' in diagnostics
    assert hasattr(_DiagnosticWebPage, 'javaScriptConsoleMessage')


def test_safe_external_fallback_is_available():
    import inspect
    from lsbpipe.gui.survey_explorer import SurveyExplorer
    source = inspect.getsource(SurveyExplorer._build_ui)
    assert 'Aladin in browser' in source
    assert 'https://aladin.cds.unistra.fr/AladinLite/' in source
