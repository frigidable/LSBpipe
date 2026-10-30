# UX baseline audit — Survey Explorer v0.4

Date: 2026-10-30
Source: `feature/survey-explorer-v04`, commit
`a2a4cb02492e4d047cb071389f970d749806c35d` before this UX branch.
Method: repository source and documentation inspection. **No application
screenshot was captured or visually reviewed at audit time.** The P0
capture job is intended to supply this missing evidence.

## Verified implementation facts
- Primary GUI is `src/lsbpipe/gui/survey_explorer.py` (~31 kB), a
  `SurveyExplorer(QMainWindow)` with embedded `QWebEngineView`.
- Default main window is 1460 x 880; horizontal splitter begins at
  [650, 810]. Left side contains target coordinates, releases, viewer
  controls, catalog summaries, brick table, product selection table,
  coverage parameters, transfer controls and progress.
- `show_viewer()` loads the hosted Legacy Viewer when the window is
  created. The application has an explicit external-browser fallback.
- Filtered file rows retain selection; `select_science_quartet()`
  explicitly clears selection outside the chosen quartet.
- Survey discovery/download operations run in a Qt worker thread.
- Existing workflow `.github/workflows/survey-tests.yml` runs
  scientific tests on Windows/Linux and a Windows import-only GUI check.
- The hosted Viewer does not expose a guaranteed two-way pan/zoom
  synchronization contract; downloaded FITS are the scientific products.
- Documentation explicitly states that real on-device GUI/coverage
  validation for latest work is incomplete.

## Risks from source inspection (not measured defects)
P0 / usability:
1. Left panel combines too many independent decisions in one dense vertical
   flow. At narrow heights controls may be clipped or difficult to find.
2. A custom splitter without dedicated task workspaces risks placing
   file-selection controls and the science preview in competition.
3. Multi-step transfer and coverage dialogs create context switches;
   important diagnostic states may be lost among transient messages.
4. Critical scientific distinctions are mostly long paragraphs/tooltips
   rather than contextual, semantically distinct status treatments.
5. The `SurveyExplorer.width` field shadows `QWidget.width()`; UI tests must use `window.size().width()` until a dedicated refactor can rename it safely.
6. Network, archive discovery and view navigation are adjacent in
   `SurveyExplorer`, making independent UI state testing harder.

P1 / engineering:
7. The main GUI class couples widget construction, callbacks, network
   orchestration and response formatting; targeted refactoring should
   reduce the UI-only regression surface.
8. There is no reproducible screenshot coverage in CI, so visual
   regressions are not represented in ordinary test results.
9. WebEngine load success is not evidence that tiles rendered or that a
   particular release has complete coverage.

## Do not infer
No claim is made here that controls *actually* overlap, that the app is
unusable on Windows, or that its visual appearance scores a particular
number. Those require captured screens and scenario-level evaluation.

## First quality experiment
Produce deterministic, offline image captures at 1366x768,
1600x900, and 1920x1080. Do not perform network requests or represent
blank WebEngine canvas as rendered survey imagery. Record widget
bounds and artifact provenance alongside each capture.
Review screenshots; then establish approved reference images and only
later enforce meaningful regression thresholds.

## Existing science constraints to retain
See `docs/SURVEY_EXPLORER_V04.md`, `docs/COVERAGE_P2.md`,
`docs/ARCHITECTURE_DECISIONS.md`. UI work must not silently override
the project's existing scientific adoption and provenance contracts.
