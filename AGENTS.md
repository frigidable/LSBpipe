# LSBpipe contribution contract (ChatGPT + GitHub)

These instructions apply to any contributor or ChatGPT session working in this repository.
Read `docs/ux/PRODUCT_BRIEF.md`, `docs/ux/DESIGN_SYSTEM.md`,
`docs/ux/SCREEN_STATES.md`, `docs/ux/QUALITY_GATES.md`, and
`docs/ux/AUTONOMY_PLAYBOOK.md` before a GUI change.

## Product intent
LSBpipe is an experimental scientific workflow for low-surface-brightness
photometry using calibrated Legacy Survey FITS. Optimize the GUI for an
astronomer's decisions, not for exposing every Python function as a button.
The current PySide6 Survey Explorer is a real working vertical slice;
do not advertise planned photometry/sky/mask modules as implemented.

## Scientific safety (non-negotiable)
- Preserve the numerical core, scientific conventions, public CLI,
  data models, provenance, and checks unless a **separate** science-reviewed
  change is expressly requested.
- Online Legacy Viewer imagery is a **preview**, never an accepted science
  image. The native, validated FITS quartet is the science input.
- Brick intersections, median NEXP and successful HTTP HEAD checks must not
  be reported as per-pixel coverage or successful downloads.
- DR9/DR11 North observations may be related; do not claim independent data.
- Preserve explicit release, band, ICRS/WCS, mask/IVAR/NEXP interpretation,
  units and provenance. Never silently fabricate measurements, fill masked
  flux or turn a diagnostic into an adopted photometric result.
- Keep statuses `EXPERIMENTAL_NOT_ADOPTED` /
  `DIAGNOSTIC_NOT_ADOPTED` wherever currently applicable.
- Do not silently select downloads or issue network requests beyond
  documented explicit user actions. Preserve original science FITS bytes.

## Autonomous design authority
For an approved GUI task, choose typography, spacing, theme, layout,
responsive behavior, icons and interaction patterns without asking the
user to approve every cosmetic decision. Favor a scientific workbench:
large image canvas, compact navigation, contextual inspector, trustworthy
status/progress surfaces, keyboard access, and discoverable recovery.
Use the tokens and component conventions from DESIGN_SYSTEM.md.
Keep error and caution messages immediately visible and actionable.
No cosmetic simplification may erase scientific qualifiers.

## Development and review protocol
1. Inspect actual source, tests and existing project contracts.
2. Record evidence and acceptance cases before changing the GUI.
3. Work on a dedicated feature branch; never edit or merge `main` without
   explicit approval. Keep unrelated science PRs separate.
4. Separate Qt presentation from survey/network/photometry logic.
5. Add or update offline GUI tests for empty, populated, loading,
   error and resized states; avoid real network in deterministic CI.
6. Produce actual screenshot artifacts and a machine-readable manifest.
   A passing test or screenshot file is NOT visual approval.
7. Review screenshots when available and document layout faults, clipping,
   contrast issues, workflow friction and limitations. Make repair cycles
   before proposing acceptance.
8. Run scientific regression tests and record the exact observed status.
   If a test was not executed, state `not run`; never invent success.
9. Use draft PRs until all blocking gates are met; never self-merge.
10. Report the branch, files changed, checks actually executed, visual
    evidence inspected, remaining risks and clear next step.

## Context and tooling
This project is developed through ChatGPT + connected GitHub, not through
a mandatory Codex, Figma or local designer workflow. GitHub Actions can
execute tests automatically after pushes, but an ordinary chat does not
continue working on its own after the conversation ends. Do not imply
background screenshot review, ongoing autonomous coding, or certification
without a real executor and observed outputs.
