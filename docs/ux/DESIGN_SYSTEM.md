# LSBpipe Scientific Workbench — design system v0.1

Status: proposed tokens and patterns, **not yet rendered or approved**.
Implemented Qt widgets must be measured against actual screenshot evidence
before this becomes a visual-regression baseline.

## Visual intent
A precise scientific workstation rather than a marketing dashboard.
The FITS canvas and evidence occupy primary visual attention. Quiet,
consistent panels carry controls and metadata. Avoid gradients as a
substitute for hierarchy, unnecessary cards, and oversaturated badges.

## Color tokens (hex)
| Role | Dark | Light |
| --- | --- | --- |
| Window | #111923 | #F6F8FC |
| Panel | #182331 | #FFFFFF |
| Elevated | #223143 | #E9EEF5 |
| Primary text | #E8F1FA | #172232 |
| Secondary text | #AAB9C8 | #415569 |
| Interactive accent | #59B7FF | #1C5D99 |
| Warning text | #F8C678 | #8C4F00 |
| Error text | #FF9E9E | #9B2F3A |
| Success text | #81D4A3 | #176641 |
| Border | #334558 | #CAD4DF |

The primary/secondary/accent pairs above have been numerically checked
against their primary panel/window backgrounds for >= 4.5:1 text contrast.
This does not certify every hover, disabled, overlay or state combination:
verify actual rendered foreground/background pairs before shipping.
Never rely on hue alone for scientific data categories or warnings.

## Typography and geometry
- Font: system UI (Segoe UI on Windows), fallback Noto Sans / sans-serif.
  Preserve astronomical symbols and Greek characters correctly.
- Base body: 13–14 px at 100%; dense table: 12–13 px; title: 18–20 px;
  smaller metadata: >= 12 px where practicable.
- Spacing: 4, 8, 12, 16, 24, 32 px; radii 4/8 px; consistent borders.
- Controls: nominal >= 32 px high (36 px for primary actions);
  do not shrink interactive hit regions to fit a crowded sidebar.
- Use a single primary action per state; secondary actions subdued but
  discoverable. Labels describe actions, not internal method names.
- Support resizable windows, high DPI, font scaling and keyboard focus.
  Never encode pixel fixed heights as the only way to display controls.
- Splitter state must be adjustable and recoverable. Scrollable inspectors
  are preferable to hidden controls on a short screen.

## Information hierarchy
Default layout (only when viewport permits):
1. Compact top row: project/target, release/band, data source, task state.
2. Left navigation: Survey, Data, Scene, Background, Photometry, Results
   according to actual capability.
3. Center: largest continuous surface for hosted preview/local FITS
   visualization; clear preview/FITS status and coordinate scale.
4. Right contextual inspector for current task and relevant options.
5. Collapsible bottom run/status evidence log (do not force modal dialogs
   for routine non-blocking messages).

At ~1366x768, keep task hierarchy usable via scroll/stacking; do not
assume all panes can remain full width. Avoid nested permanent toolbars.

## Reusable Qt components (target contracts)
- `TargetHeader`: ICRS, selected release, selected band, source provenance.
- `WorkspaceNavigation`: capabilities, not speculative runnable modes.
- `ViewerSurface`: hosted preview vs local FITS explicitly separated.
- `ContextInspector`: only properties relevant to the current operation.
- `EvidenceStatus`: status + source + uncertainty + recommended action.
- `ScienceFileTable`: product identity, release/region/brick/band,
  evidence, known or unknown size, selection persistence.
- `TaskProgress`: quantitative progress only when measured; safe retries.
- `QualityReport`: diagnostic vs adopted status and source evidence.
Do not create components solely because this list exists. Extract them
when a real screen has a working scenario and accompanying tests.

## Scientific visualization contracts
- A celestial image must expose FITS source, band, coordinate convention,
  scale and displayed stretch without changing underlying pixel data.
- Image, IVAR, NEXP, MASKBITS, derived model and residual are distinct
  layers with separate provenance. Displayed previews are not analysis data.
- Overlay geometry must preserve WCS alignment; changing image source
  invalidates stale overlays unless reprojected/verified.
- Distinguish valid, missing and masked pixels; negative measured flux
  remains negative. Never clip negative data as a scientific transform.
- Plots label axes with physically correct units, uncertainty conventions,
  and clear data/model/residual encoding; default to accessibility-friendly
  patterns, line styles and symbols.

## UX language
- "Preview (remote)" / "FITS (local)" instead of ambiguous "Image".
- "Candidate file" / "HTTP checked" / "Downloaded" are different stages.
- "Coverage diagnostic — not adopted" for local coverage screening.
- Prefer explicit, repairable error messages to catch-all failure dialogs.

## Influence and licensing
Use official Qt accessibility principles, napari scientific-layer
interactions, and established research-software UX patterns as conceptual
references; do not copy third-party proprietary skins or assets.
Qt/PySide6 remains the first implementation target.
