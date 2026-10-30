# LSBpipe v0.4 P0 — Survey Explorer (experimental)

This is the **first working integration slice**, created on `feature/survey-explorer-v04` from the `main` baseline. It does not depend on the unmerged v0.3 photometry branch and does not modify any of its scientific algorithms.

## Windows installation and launch

In PowerShell:

```powershell
cd C:\Users\frigi\Documents\PhD\LSBpipe
git fetch origin
git switch --track origin/feature/survey-explorer-v04
python -m pip install -e ".[exact,test,gui]"
python -m pytest -q
python -m lsbpipe.gui
```

If the local branch already exists: `git switch feature/survey-explorer-v04` then `git merge --ff-only origin/feature/survey-explorer-v04`.

Dependencies: Python >=3.10, PySide6 with QtWebEngine, Astropy; QtWebEngine and Aladin need internet for remote JavaScript, WebAssembly and HiPS tiles. A working browser-based map does not imply the requested Legacy release has coverage.

## Workflow

1. Enter **ICRS decimal RA/Dec**, square field width (arcmin) and select DR9, DR10 and/or DR11.
2. An embedded **Aladin Lite v3** displays a DSS2 reference sky layer. It is not falsely labeled as a particular DESI data release. Move/zoom, or choose `Use Aladin center` to copy its center back into the RA/Dec fields. The currently requested brick rectangles are overlaid in green.
3. Click **Find Legacy data**. The first request downloads/cache-stores the release's `survey-bricks.fits.gz` geometry, and its `{region}/survey-bricks-{release}-{region}.fits.gz` footprint summaries. First use can be much slower or download significant catalog data. The code searches every intersecting **covered release-summary brick**; a geometrical brick alone is NOT counted as coverage.
4. The summary shows the brick's median `NEXP_g/r/i/z` from its primary area (a missing northern `i` column is shown as unavailable). A median of zero does not prove absence of all exposure data in the brick. Actual pixel coverage will require downloaded `nexp` and `invvar` maps.
5. Choose a download profile and inspect its **candidate file URLs**. `Check selected files` issues HTTP HEAD requests and distinguishes `HTTP_OK`, `HTTP_MISSING`, `HTTP_UNVERIFIED` and `HTTP_ERROR`. HEAD may be unsupported by an otherwise working archive; only an actual successful download is evidence of retrieved bytes.
6. Click **Save manifest** to store a human-readable provenance plan. `Download selected` asks for a local directory, retrieves original archived FITS files via HTTPS, writes temporary `.part` files and finally an atomic rename; stores per-file SHA256, byte counts, and failures in `download-report.json`. Existing local files are reused but **not** asserted to match the current upstream source.
7. The first version does not interpolate, mosaic or photometrically alter the archived bricks.

Supported types: `image`, `invvar`, `maskbits`, `nexp`, `tractor`; the extended profile adds `psfsize`, `depth`, `galdepth`, `model`. Tractor is a **source catalog**, not a foreground-mask image. Download `maskbits.fits.fz` only once per brick (it is not band-specific); for optical maskbits the proper extension is generally **HDU1**, not HDU0. For science-ready LSB surface photometry, also assess PSF wings, spatially correlated noise and foreground overmasking.

A brick can have files in multiple releases or regions. The explorer deliberately does **not** silently deduplicate them across releases. DR10 has south only; DR9 and DR11 have north and south but the north does not provide `i`-band. DR11 north can reuse DR9 northern imaging, so results from multiple releases are **not independent datasets**.

## Command-line equivalent (no Qt)

```powershell
python -m lsbpipe.surveys discover --ra 154.7709 --dec 46.4537 --width-arcmin 15 --release dr9 --output data-plan.json
python -m lsbpipe.surveys probe --manifest data-plan.json --output data-verified.json
python -m lsbpipe.surveys download --manifest data-verified.json --directory C:\LSBpipeData
```

The manifest does **not** authorize downloads by itself. The `download` command explicitly initiates data transfer. CLI `probe` may generate many network HEAD requests; restrict the FOV if needed. The desktop interface provides file-level selection.

## Known scientific/engineering limits

- The field-to-brick test is a **conservative RA/Dec bounding-box intersection**, not an exact spherical polygon intersection. Near the celestial poles this can overselect bricks; high-latitude selections deserve additional review.
- Survey summary median NEXP is not the fraction of field pixels covered. Archive presence is not evidence of quality or depth sufficient for a particular LSB galaxy.
- This initial prototype does not create cross-brick mosaics, resolve multiple releases into a single definitive observation, download CCD-level data, run Tractor models, or generate automatic masks from Tractor.
- SHA256 is a local fingerprint; no authoritative upstream checksum comparison is performed. File starts with a FITS primary header signature; full HDU/data validation is deferred to the existing Astropy FITS ingestion stage.
- Network connectivity, HTTPS proxy/VPN settings, and the remote availability of Aladin assets vary by machine. GUI still requires on-device visual QA; automated tests do not confirm that a QtWebEngine viewport successfully renders on every Windows configuration.
- Catalogs are cached under `%LOCALAPPDATA%\LSBpipe\catalogs` on Windows. There is no automatic expiry; users must consciously refresh the cache to see newly replaced archive catalogs.
- The allowed download list is capped (48 brick hits, 300 files) to prevent accidental mass requests. Large-area survey batch mode requires a separate interface and throttling.
- Aladin Lite is a CDS product (GPL-3); retain its attribution/logo and review license requirements before distributing proprietary binaries.

## Validation

The PR includes deterministic, offline tests for RA=0° geometry, release and band restrictions, correct NERSC path construction, catalog membership, manifest flags, HEAD responses, HTTP 404 and atomic FITS downloads. It also adds a Windows/Linux GitHub Actions matrix and a Windows PySide6 import smoke test. Neither real survey downloads nor GUI rendering are claimed as verified until a successful online Windows run is demonstrated.

References: https://www.legacysurvey.org/dr9/files/ ; https://www.legacysurvey.org/dr10/files/ ; https://www.legacysurvey.org/dr11/files/ ; https://aladin.cds.unistra.fr/AladinLite/doc/

## Aladin Lite fails to initialize (Windows / QtWebEngine)

An empty sky panel and `Loading Aladin Lite JavaScript from CDS…` mean
the browser has not completed loading the remote script. This **does not**
implicate DESI catalog discovery, which uses a separate Python HTTPS client.

Choose **Aladin diagnostics**. As of the latest GUI implementation, diagnostics
return a JSON string through Qt, not a raw JS object (which was previously
incorrectly reported as a missing bridge). The report includes HTML readiness,
current script/loader stage, `window.A` and Aladin availability, WebGL2 status,
Qt page URL and recent JavaScript-console messages.

Use the **Aladin in browser** fallback to distinguish an embedded WebEngine
problem from general reachability of the CDS web service.

To check HTTPS access through *Python* separately:

```powershell
python -c "import urllib.request; u='https://aladin.cds.unistra.fr/AladinLite/api/v3/latest/aladin.js'; r=urllib.request.urlopen(u,timeout=12); print(r.status, r.headers.get('Content-Type'), r.read(20))"
```

If this Python check works but embedded JavaScript remains at
`script_loading`, investigate Chromium/QtWebEngine proxy settings, firewall,
certificates and the JavaScript console. If Python also times out, investigate
network/host access before making additional code changes. The presence of
WebGL2 does not by itself prove that WASM assets loaded.

Do not disable browser security, TLS certificate verification or Windows
firewall to work around the error. Full GUI rendering requires a separate
on-device test; offline Pytest/CI checks cannot certify it.
