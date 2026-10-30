# Survey Explorer redesign evidence — 2026-10-30

## Identified source

- Repository: `frigidable/LSBpipe`.
- PR: https://github.com/frigidable/LSBpipe/pull/4
- Code snapshot: `b864e9f4af9cd070be039ca40105e889427b5012`.
- Successful Windows GitHub Actions run:
  https://github.com/frigidable/LSBpipe/actions/runs/41114708071
- Screenshot ZIP:
  https://github.com/frigidable/LSBpipe/actions/runs/41114708071/artifacts/13006240647
- Screenshot fixture: four **synthetic offline** states S0–S3; hosted
  Legacy Viewer substituted with a blank QWidget. No remote survey tiles,
  scientific FITS, downloaded products or astrophysical measurements.
- Tests: **103 scientific tests passed; 10 GUI smoke tests passed**.
- 12 screenshot PNGs, 12 per-screen geometry JSONs and a manifest created.
- Windows CI capture font: **Segoe UI**, registered from installed system
  font files (Segoe UI / Arial / Tahoma); screenshot glyphs verified legible.

## Verified geometry

All captures retained requested exact window width and height. The viewer
split is reported relative to the top-level window; it is not a measure
of useful loaded sky pixels.

| Window | S0 viewer width | S1–S3 viewer width | S1–S3 viewer height | Download action |
| --- | ---: | ---: | ---: | --- |
| 1366×768 | 72.2% | 72.2% | 502 px | visible: y=737, h=29 |
| 1600×900 | 76.3% | 76.3% | 634 px | visible: y=869, h=29 |
| 1920×1080 | 80.3% | 80.3% | 814 px | visible: y=1049, h=29 |

Historical baseline S0 at 1366×768 had only **29.4%** preview width,
and an earlier first redesign expanded its window beyond 1366px when
populated. Neither defect is present in the inspected successful capture.

The latest capture introduces explicit screenshot QA failure when:
- actual PNG dimensions differ from the requested logical dimensions;
- preview width share falls below 55%;
- the selected download action is outside the visible window in S1–S3.

## Pixel-level visual review

The 1366×768 S1 capture was examined at native resolution. A contact
sheet containing all S0–S3 screenshots across three target sizes was
also inspected.

**Confirmed improvements:**
- text is now legible in the offscreen capture;
- remote preview provenance is continuously displayed;
- the filename/HTTP-status workbench no longer displaces most horizontal
  canvas space;
- the bottom download and manifest actions are pinned and visible;
- the drawer toggle is near the top, accessible when the lower dock is open;
- 88 synthetic product rows and hidden selections remain represented
  without causing window enlargement.

**Open UX shortcomings:**
- At 1366×768 with the bottom dock open, the left brick table has
  little visible height; the navigation area must scroll to inspect all
  release records.
- The lower file table exposes only a few rows at this dock height.
  This is functionally scrollable, but the user may choose to enlarge the
  dock for batch selection.
- No evidence for real monitor high-DPI/125–150% font scaling or keyboard
  focus order yet; offline screenshots do not prove accessibility.
- A blank offline Viewer placeholder proves layout geometry only; it
  does not verify native hosted Viewer controls, mouse navigation,
  online tile availability, or science data.

## Decision

The image-first reflow is sufficiently supported by offline geometry
and synthetic interaction evidence to proceed to **on-device QA**.
Keep PR #4 **Draft** until the real Windows application with hosted
Viewer and file selection confirms the interaction experience. This
does not approve scientific photometry or change the scope of PR #2.

Next stages: real-Windows visual check and scrolling, keyboard/high-DPI
checks, then S4–S10 negative-path fixtures and layout refinement.
