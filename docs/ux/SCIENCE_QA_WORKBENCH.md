# Science QA in the LSBpipe desktop GUI (experimental)

Branch: `feature/science-qa-gui` (Draft PR #6), based on the original-FITS
pilot branch `feature/real-fits-pilot-p3` (Draft PR #5). Do not merge
into `main` before scientific and on-device visual acceptance.

## Research workflow

The GUI has two **right-side inspector tabs**: `Archive files` retains
HTTP verification, file filters, science quartet selection, manifest and
download; `Science QA` exposes already-tested P3 original-FITS operations.
The center has three explicitly distinct tabs:
`Remote sky` (official online Legacy Viewer, NOT calibrated FITS), and
Four-layer QA (a static figure from original FITS) and FITS Explorer
  (a native, interactive pixel-level display from the same originals).
Switching between these tabs must never mutate the science data.

From the left brick table, select one release/region/brick. The right
Science QA panel inherits its explicit provenance and offers an optical
band, a downloaded FITS folder, and five distinct operations:

- Check full search square: 5' TAN-square WCS coverage, IMAGE/INVVAR/
  NEXP/SATUR/ALLMASK fractions and named MASKBITS census.
- Locate mask footprints: connected SGA GALAXY, Gaia MEDIUM and
  band-specific saturation components with ICRS positions and radii.
- Explore original FITS interactively: a native zoomable scientific pixel
  canvas with scroll-wheel magnification, hand-drag panning, IMAGE/INVVAR/
  NEXP/MASKBITS layer switching, faint-feature contrast preset, and
  independently toggleable saturation, Gaia MEDIUM and SGA GALAXY bit
  overlays. The exact original (x,y) pixel index, ICRS RA/Dec, flux in
  nanomaggies, inverse variance, NEXP and individual maskbit names appear
  on mouse hover. All overlays are *view-only*.
- Export four-layer FITS map: WCS-registered IMAGE, INVVAR, NEXP and
  categorical MASKBITS; saves and previews a static publication-audit PNG.
- Compare DR9 North/South: strictly paired original coadds with same
  WCS and pixel centers. Separate North and South folders are required.

The workbench accepts either the `data/` root itself or the parent pilot
folder containing `data/`. It checks that the expected four original
files exist for the exact selected release/region/brick/band. It never
automatically downloads, silently changes the survey region, reprojects
mismatched WCS, or makes a photometric acceptance decision.

Every successful Science QA computation is written to a NEW, write-once
run directory **next to the `data/` input directory**:
`<pilotRoot>/qa-runs/<UTC-timestamp>-<random-id>/`.
This folder contains the diagnostic JSON or PNG and `manifest.json`,
including full field, band, aperture parameters, native input FITS paths,
local SHA-256 fingerprints, artifact SHA-256 and the pipeline version.
Local checksums do not verify authoritative remote archive integrity.
Original FITS inside `data/` are not overwritten. In-progress runs
are staged privately and published only upon successful completion.
The right pane gives readable summary text with a button to open the
saved JSON/PNG. Computations run in a worker QThread and should not
freeze the desktop or force a remote map reload.

## First actual Windows GUI validation

Start in the current Windows source checkout:

    cd C:\Users\frigi\Documents\PhD\LSBpipe
    git fetch origin
    git switch --track origin/feature/science-qa-gui
    python -m pip install -e '.[gui,exact,demo,test]'
    python -m pytest -q
    python -m lsbpipe.gui

Then enter ICRS RA 40.096208, Dec +1.229250, search area 5 arcmin,
and DR9. Click Find data. In the left table select **DR9 South**
brick 0401p012, then open the right-side **Science QA** tab.
Choose the folder
`C:\Users\frigi\Documents\PhD\LSBpipeData\pilot_0401p012_south_r`
or its `data` child. Select r band.

Run `Check full search square`, `Locate mask footprints`, and
`Explore original FITS interactively`. Switch the four layers and
mask overlays, inspect cursor ICRS/pixel coordinates, and separately
click `Export four-layer FITS map`. All results remain labeled
`DIAGNOSTIC_NOT_ADOPTED`. Confirm the PNG preview appears in the
`Four-layer QA` tab, that `FITS Explorer` renders separately,
and that the `Remote sky` tab still works,
and that no data are downloaded or modified.

For the paired test choose the *separate* original North pilot folder
`C:\Users\frigi\Documents\PhD\LSBpipeData\pilot_0401p012_north_r`
and the separate South folder under North / South originals.
Click `Compare DR9 North / South`. It must reproduce:
`1,286,594 both valid`, `22,122 South-only`, `0 North-only`,
`2,309 invalid in both`, and `1,311,025 paired sky pixels`,
provided the original FITS are the same as the archived pilot.

## Explicit limitations

This GUI integration reuses existing science procedures, including
their conservative instrumental masking policy. There is no
background model, segmented contaminant mask, stellar PSF halo
solution, science-ready mosaic, independent-pixel noise assumption
or Sérsic photometry here. The P3 science reports and original
DR9 FITS remain unchanged and are the source of scientific truth.

Offline PySide6 tests protect keyboard input, layout, error/stale
guards and central figure switching; live Windows on-device
screenshots and real FITS behavior are the final UX acceptance gate.


## S5 interactive native-viewer visual test

The Windows offscreen capture harness now exercises S5 at 1366×768,
1600×900 and 1920×1080. The underlying simulated galaxy, stars,
photometric layers and MASKBITS are explicitly **synthetic fixtures**.
The automatic screenshot verifies visible Qt tabs, controls, image
raster and no window-width overflow; it is not evidence about a real
galaxy, instrument PSF or science-mask adequacy. Pixel readout,
FITS-to-Qt y-coordinate reversal and transparency are also tested
against deterministic in-memory arrays.

## P0 context and audit-data safety (implementation)

The GUI takes a frozen `ScienceContext` snapshot for each request, covering
action, ICRS geometry, brick, band, target/sky radii, and North/South/local
source folders. Changing any of these inputs increments a GUI generation
counter and clears the current scientific figure and report action.
A worker result is displayed only when both the original context
and its submission generation still match; changing an input and
then restoring the old value does **not** revalidate a stale result.
For saved scientific reports, the backend additionally validates
the frozen input context before any FITS read, checks all four
(or eight for paired North/South) local input hashes and checks
their integrity again before atomically publishing the run. This is
a scientific evidence gate, not a guarantee of access isolation
from arbitrary external file writes on the user's Windows machine.

Regressions test unique run IDs, unchanged earlier records after a second
run, failed runs leaving no completed record, modifications to input
FITS with unchanged size/mtime, stale in-flight results and selection
changes. This does not yet constitute a real Windows end-to-end
acceptance of the new GUI, which will be performed later.

## P0.4 — on-demand rendering and bounded display memory

Before this change, every opened original-FITS quartet produced four
eager full-resolution RGB copies in the data-loading worker, and
changing contrast or overlays could allocate large render buffers
on the Qt UI thread. The interactive FITS Explorer now:

- retains the four original numerical arrays for exact ICRS-pixel
  readout; they are marked read-only and never modified;
- avoids precomputing RGB for the four layers; one selected layer
  and overlay are rendered on demand;
- uses a single Python background render worker for cutouts larger
  than 120,000 pixels (Qt Pixmap creation stays on the UI thread);
- coalesces rapid layer/contrast/flag changes, using a generation
  token to reject late images from obsolete datasets or controls;
- uses a 48 MiB byte-budgeted LRU for display-only RGB/RGBA buffers;
- displays the current display-cache usage and scientific viewing
  state while each layer is prepared;
- refuses a full-frame interactive image larger than 6,000,000
  original pixels with an explicit explanatory error. This is a
  temporary safety limit, **not** a tiled viewer or automatic resample.

Pixel readout remains strictly tied to original FITS values and WCS.
Small synthetic images are rendered synchronously to retain precise
test assertions. Tests also exercise larger asynchronous arrays,
rapid changes, cache eviction, removal of pending science views,
and failure to silently downsample oversized images.

**Limitations:** the raw four-FITS arrays and temporary rendering
buffers are not counted within the 48 MiB *display cache* cap.
Peak process memory has not yet been benchmarked on the user's
real Windows FITS; a future tile/pyramid renderer will be needed
for images larger than the current full-resolution cap.

## P1.1 — progressively organized Science QA controls

The science inspector now keeps the selected brick/band and original
FITS data root visible at the top, followed by a three-stage workflow:

1. Inspect FITS: square coverage, interactive image, four-panel export.
2. Review masks: connected SGA, Gaia and saturation footprints.
3. Compare reductions: separate original North and South data roots
   and strict pixelwise paired comparison.

Each step has an explicit Running / Diagnostic saved / Failed /
Viewer ready status; statuses reset when source context changes.
Scientific result summaries, run ID, JSON/PNG and provenance manifest
remain accessible below the workflow. This is a navigation structure
only: no sky subtraction, photometric source masking or scientifically
adopted surface brightness is introduced.
