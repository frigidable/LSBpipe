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

Dependencies: Python >=3.10, PySide6 with QtWebEngine, Astropy; the hosted Legacy Survey Viewer requires network access. A visible viewer image is not proof of complete per-pixel coverage of a requested release.

## Workflow

1. Enter **ICRS decimal RA/Dec**, square field width (arcmin) and select DR9, DR10 and/or DR11.
2. **Official Legacy Survey Viewer** is loaded directly as the main page in QtWebEngine, with RA/Dec, the chosen imaging release/layer and a target marker encoded in the URL. **Show target in viewer** reloads at the typed coordinates; **Open in browser** launches exactly the same URL externally. This is the hosted Imagine/Leaflet viewer: it is not an iframe, not Aladin, and LSBpipe does not inject code into its internals. The displayed zoom is a viewer zoom level, distinct from the square field size for brick discovery. Brick polygons currently appear only in the LSBpipe left-hand table, not as overlays inside the remote viewer.
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
- Network connectivity, HTTPS proxy/VPN settings, and the remote availability of Legacy Viewer assets vary by machine. GUI still requires on-device visual QA; automated tests do not confirm that a QtWebEngine viewport successfully renders on every Windows configuration.
- Catalogs are cached under `%LOCALAPPDATA%\LSBpipe\catalogs` on Windows. There is no automatic expiry; users must consciously refresh the cache to see newly replaced archive catalogs.
- The allowed download list is capped (48 brick hits, 300 files) to prevent accidental mass requests. Large-area survey batch mode requires a separate interface and throttling.
- The hosted Legacy Viewer is a third-party web application. Keep its branding/attribution, review its terms for deployment, and do not assume it exposes a stable JavaScript API for extracting the moved map center.

## Validation

The PR includes deterministic, offline tests for RA=0° geometry, release and band restrictions, correct NERSC path construction, catalog membership, manifest flags, HEAD responses, HTTP 404 and atomic FITS downloads. It also adds a Windows/Linux GitHub Actions matrix and a Windows PySide6 import smoke test. Neither real survey downloads nor GUI rendering are claimed as verified until a successful online Windows run is demonstrated.

References: https://www.legacysurvey.org/dr9/files/ ; https://www.legacysurvey.org/dr10/files/ ; https://www.legacysurvey.org/dr11/files/ ; https://github.com/legacysurvey/imagine ; https://www.legacysurvey.org/svtips/

## Legacy Viewer integration and troubleshooting

LSBpipe previously used Aladin Lite v3, but on the user's Windows machine
the CDS-hosted JavaScript failed to load inside QtWebEngine. The default
viewer has therefore been replaced by the **official Legacy Survey Viewer**,
loaded as a top-level `QWebEngineView` page.

The official viewer supports URL arguments `ra`, `dec`, `zoom`,
`layer` and `mark`. Its layer choices in `lsbpipe.surveys.viewer`
include DR9 North/South, DR10 South, DR11 South, and combined release layers.
DR11 northern imaging is the reused DR9 North imaging; it must not be
represented as an independent DR11 image.

Example: https://www.legacysurvey.org/viewer/?ra=154.7709&dec=46.4537&zoom=13&layer=ls-dr9-north

**Show target in viewer** navigates within Qt; **Open in browser** is a
separate fallback for computers where embedded Chromium networking fails.
**Viewer status** reports the actual Qt URL, HTML readiness, number of loaded
images, script count, and JavaScript console output.

The viewer's pan/zoom state is currently independent of LSBpipe's typed
RA/Dec. The stable URL interface permits one-way navigation, but cannot
guarantee reading the changed center back from the remote website. Use
the viewer's **Link Here** tool to recover coordinates, or enter coordinates
in LSBpipe's own form. No custom remote JavaScript integration is assumed.

The viewer is an exploratory interface, *not* a scientific data source for
`ObservationSet`. LSBpipe obtains unmodified FITS products from the release
archive and uses its own provenance, WCS and validity checks. Viewer JPEG
cutouts (max side length generally 512 pixels) are suitable for preview, not
a replacement for original brick-level coadd science products.

If QtWebEngine fails to load Legacy Viewer but the link opens in the
external browser, keep the research process functional using the browser
fallback. Check `Viewer status` for document errors and graphics/network
issues before changing proxy/certificate settings; never disable TLS checks.

### Validation status

URL generation and GUI integration have offline unit tests; on-device
rendering and network retrieval still require a Windows user validation.

## UX P1 — compact explorer and preview controls

The `Survey Explorer` v0.4 development branch also includes these refinements:

- **Viewer mode:** Image / Tractor model / residual, using documented
  `-model` and `-resid` Legacy Viewer layer suffixes. These are *preview*
  products, not replacement inputs for calibrated LSB measurements.
- **Web scale:** adjusts the embedded QtWebEngine page zoom independently of
  the viewer's astronomical map zoom or the requested brick-search area. The
  initial scale is 0.80 to make hosted Legacy controls fit better on a Windows
  desktop; the researcher can increase or decrease it.
- **Brick preview:** double-click an intersecting brick in the left table to
  navigate the viewer to its RA-wrap-safe midpoint. The typed target RA/Dec
  and the science `DownloadManifest` are not changed by this preview.
- **Download table:** short columns for brick, product kind, band, HTTP
  evidence and size; hover over a product to see its full archive-relative
  path and HTTPS address.
- **Filters:** brick, band and product kind. Filtering only hides rows; it
  does not silently alter the selected set. The `g/r/i/z` band filter retains
  files shared across bands (`maskbits`, `tractor`). **Select visible** and
  **Clear visible** affect only displayed rows.
- **Download preflight:** an explicit confirmation shows the number of selected
  files, verified/missing/unverified HTTP states, and the *known* size. An
  unknown size is never counted as a confirmed zero. `HEAD` responses are
  informational and can be unavailable on some otherwise working hosts.

The selected set persists when filtering; changing from Standard to Extended
keeps the previously selected files and introduces extra product kinds
unchecked. A new target search creates a fresh candidate selection.

**Known limitations:** The hosted Legacy Viewer cannot presently send mouse
pan/zoom state back to Python. Changing Viewer mode or revisiting the target
reloads the hosted page, so in-browser manual pan position is not retained.
The product count/known size is a planning estimate, not proof that all remote
content exists or can be recovered. Automatic mosaicking, per-pixel NEXP/IVAR
coverage plots, automatic Tractor-derived exclusion masks and scientifically
calibrated QA remain later milestones.
