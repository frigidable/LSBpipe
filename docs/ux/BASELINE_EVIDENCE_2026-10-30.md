# Survey Explorer P0 — measured screenshot evidence

Audit date: 2026-10-30
Repository: frigidable/LSBpipe
CI commit: `acb4048306962f87ccaf82b7ef3c333241818765`
GitHub Actions run: https://github.com/frigidable/LSBpipe/actions/runs/41110944018
Screenshot artifact: https://github.com/frigidable/LSBpipe/actions/runs/41110944018/artifacts/13003392857

## Executed results (not estimates)
- Scientific regression suite with Astropy/Photutils: **103 passed**.
- Offline GUI smoke suite: **5 passed**.
- Baseline capture: 3 valid PNGs, 3 corresponding JSON geometry reports,
  and an index manifest uploaded as a ZIP artifact.
- Capture uses an **offline QWidget placeholder** instead of Qt WebEngine;
  no remote survey tiles, real local science FITS, or network results shown.

## Objective layout measurements
| Requested window (logical px) | Left controls px | Preview px | Preview share |
| --- | ---: | ---: | ---: |
| 1366×768 | 896 | 466 | 34.1% |
| 1600×900 | 896 | 700 | 43.8% |
| 1920×1080 | 1006 | 910 | 47.4% |

There is a 4-px splitter between columns. At 1366×768, ~65.6% of
horizontal space is taken by controls, while only ~34.1% is left for
the scientific preview. This confirms **insufficient viewer priority**
at laptop resolution. A design correction is justified regardless
of theme or font choice.

## Actual visual review
The 1366×768 and 1600×900 images were opened and visually inspected.
They show a single dense column containing the target form, viewer
controls, brick table, file table, coverage and transfer sections.
A large portion of the available width is committed to forms/tables,
even in the empty state.

**Rendering caveat:** Qt offscreen Windows screenshots show square
missing-glyph boxes instead of actual characters, so typography,
label wording and font quality cannot yet be visually certified.
This is a *capture-environment defect*, not proof that normal Windows
desktop users see broken fonts. A targeted font fallback experiment
is pending. Do not use these PNGs as approved visual references.

## Conclusions and caveats
Confirmed: horizontal allocation is mismatched to scientific image
inspection; all S0 information has high visual density.

Not verified: true WebEngine imagery, real desktop font rendering,
mask overlays, populated tables, keyboard focus, clipped interactive
targets and overall accessibility. Do not award a numeric UX score.

## Next design gates
- Resolve CI font rendering if possible; preserve provenance of overrides.
- Build S1/S2/S3 offline fixtures for populated products/selection.
- Recompose main Survey Explorer into staged controls + large canvas.
- Target >=55% main-window horizontal space for scientific canvas
  at 1366×768, with controls still reachable and no scientific warnings
  hidden. Validate this goal against real screenshots.
