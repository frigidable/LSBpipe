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

Reports are saved under `data/qa-reports/` with explicit ICRS field
coordinates, angular search width and product provenance in filenames.
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
`Open four-layer FITS map`. The scientific results remain labeled
`DIAGNOSTIC_NOT_ADOPTED`. Confirm the four-layer figure appears in
the central Local FITS QA tab, that the Remote sky tab still works,
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
