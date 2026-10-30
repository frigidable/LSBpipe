# Survey Explorer UI redesign — evidence-driven implementation

Status: **experimental / Draft PR #4**. Parent branch:
`feature/survey-explorer-v04`. This redesign is GUI-only and does not
modify the science pipeline, archived FITS or survey algorithms.

## First visual cycle

The original S0 CI capture gave the scientific preview only 401 logical
pixels at a 1366px wide window (**29.4%**). The original left pane was
961px wide. An investigation showed that download/coverage toolbars and
tables forced an inappropriate combined minimum width.

Implemented changes:
- Search and brick inspection remain on a left **scrollable** survey panel,
  constrained to 290–425px, with an initial width of 375px.
- The online Legacy Viewer remains a center-priority surface. Its header
  explicitly says **REMOTE PREVIEW** and distinguishes it from calibrated
  local science FITS.
- Product selection, URL verification, download confirmation and local
  coverage parameters are now in a **collapsible bottom dock**. The drawer
  automatically opens after finding bricks. Progress and errors remain in
  the left panel, including when the drawer is hidden.
- The file drawer has its own scroll area: a long table must **never**
  resize the top-level window beyond the screen.
- Qt control heights are increased from prior native tiny defaults; system
  light/dark palette is retained.
- ICRS and release metadata, quartets, selected-but-hidden file semantics,
  transfer behavior and diagnostic status remain unchanged.
- Renamed `SurveyExplorer.width` to `field_width` so it no longer
  shadows the inherited `QWidget.width()` method.

## Offline scenario capture and caveats

The CI capture harness now generates four states at three sizes each,
with deterministic **synthetic** archive fixtures and no real network:
- S0: first launch;
- S1: eight release records (four spatial bricks), 88 candidate files;
- S2: filtered image/r-band rows with hidden selections preserved;
- S3: one exact science quartet selected.

A screenshot manifest records `state`, commit, resolved Qt font,
window geometry and widget bounds. The capture **fails** if:
1. the actual screenshot dimensions differ from requested 1366x768,
   1600x900 or 1920x1080;
2. the horizontal scientific-preview share falls below **55%**.

The first uncorrected redesign capture (`7c4f6cd1`) showed
**72.3%** preview at 1366x768 in S0, but in S1–S3 the bottom dock made
the window wider than the requested 1366px. This is a **confirmed
blocking issue**, not a successful layout. It motivated the scroll-area
repair and strict overflow assertion added afterward.

### Font capture issue

Previous Windows offscreen Qt captures displayed text as missing-glyph
squares and reported no resolved font family. The revised harness
attempts to register **system-installed Windows font files** via
`QFontDatabase.addApplicationFont`. No font bytes are committed or
redistributed. Inspect subsequent PNG output before claiming the
rendering issue fixed. If it remains, native Windows QPA or a separate
on-device screenshot may be necessary.

## Scientific/non-goal boundaries

- Hosted Legacy Viewer navigation is a *remote preview*, never the
  `ObservationSet` image from original FITS.
- No change to `download_manifest`, `SceneState`, WCS, mask bits,
  exposure/covariance science, interpretation of NEXP or quality-adoption
  policy.
- The GUI must preserve access to HEAD evidence, individual products,
  exact four-file science quartet, source/provenance, errors and the
  coverage warning `DIAGNOSTIC_NOT_ADOPTED`.
- Full real Windows/manual visual and keyboard-accessibility review is
  still needed even if offline screenshots pass.

## Remaining acceptance work

1. Inspect screenshot **pixels** for the repaired layout and font;
   update this evidence with exact SHA and actual dimensions.
2. Verify no action is clipped at 1366x768 when both viewer and drawer
   are open, and test scrolling/keyboard focus.
3. Validate online Viewer, actual field search, file selection and one
   four-FITS local coverage report on Windows.
4. Add S4–S10 negative-path fixtures separately; do not create
   fictional download progress or unimplemented cancellation.
