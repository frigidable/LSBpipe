# Survey Explorer — evidence-led redesign plan (P2)

Parent: `feature/survey-explorer-v04`. Working UI branch:
`feature/autonomous-ui-system`. v0.3 science PR is independent.
No GUI visual changes have been implemented at this stage.

## Product direction
**LSBpipe Scientific Workbench:** an image-first astronomical desktop
application. The default experience should foreground the sky field;
survey discovery, download planning and local coverage are distinct
tasks with progressive disclosure rather than one scrolling form.

## Target information architecture
- Header (compact): project/target RA, Dec, current release, source
  evidence badge and operation state.
- Center: maximum available **Preview (remote)** / **FITS (local)**
  view. These must never be confused, and only real implemented
  modes may be selectable.
- Left: small workflow navigation / target search; avoid persistent
  full-form display once the user is inspecting a field.
- Context drawer: brick summary, selection and coverage parameters;
  user can collapse it to prioritize the image.
- Bottom optional table/log drawer: candidate files, verification,
  download details, provenance and progress with explicit evidence.
- The existing official hosted Legacy Viewer remains embedded
  when it is functional; preserve external-browser fallback.

## User journeys (implement one at a time)
J1 Search: edit ICRS / releases / FOV -> show target -> discover bricks
-> compare release records, summary NEXP warnings and preview.

J2 Acquire: select brick/band -> choose science quartet or manual
files -> filter with retained selections -> verify HEAD -> explicit
download preflight -> file report. Show hidden selected count.

J3 Diagnose: choose already downloaded science quartet ->
inspect local coverage with explicit radii -> display sampled pixel
statistics + warnings + DIAGNOSTIC_NOT_ADOPTED.

## First implementation slice
1. Extract pure presentational widgets from SurveyExplorer without
   changing survey adapters or data model.
2. Introduce a compact contextual inspector and collapsible bottom
   files/diagnostic area. Keep desktop resizing flexible.
3. Maintain existing action handlers and semantics via adapter
   methods; public CLI and science core remain unchanged.
4. Add deterministic populated/error state fixtures before deciding
   on permanent visual references.
5. Capture screenshots at three resolutions. Validate that the
   scientific viewer is >=55% of available horizontal workspace
   at 1366×768, while search, confirmation and QA remain reachable.
6. Confirm keyboard focus, per-control accessibility labels,
   selection semantics, long progress reporting and no fatal clipping.
7. Keep changes in draft PR until actual screenshots and tests
   support acceptance.

## Engineering boundaries
Suggested: split `SurveyExplorer` into thin container and
`TargetPanel`, `SurveyResultsPane`, `ScienceFileTable`,
`CoverageInspector`, `TaskStatus`, `ViewerHost`.
Add only components backed by present workflows; do not generate
inactive navigation items that imply scientific features exist.
Avoid premature full migration to QML/React. Test separations before
revising technology.

## Scientific assertions that must persist
- Remote Viewer does not become a science input.
- File-selection filters never silently deselect hidden files.
- Quartet is IMAGE/INVVAR/NEXP/MASKBITS of exactly one brick and band.
- DR9/DR11 North related input evidence is not independent dataset.
- Median NEXP != pixel coverage.
- HEAD results != verified download.
- Coverage screen remains a diagnostic, not adopted photometry.
- Existing local FITS data and provenance remain unaltered.

## Definition of done
103+ existing regression tests (as applicable to updated branch),
new user-journey tests, screenshots from actual running Qt, reviewers'
evidence against QUALITY_GATES and no blocking scientific regressions.
A green CI run alone is not visual acceptance.
