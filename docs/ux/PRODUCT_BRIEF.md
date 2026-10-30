# LSBpipe Scientific Workbench — product brief

Status: UX planning contract for the currently experimental LSBpipe.

## User, purpose, scope
Primary user: researcher studying low-surface-brightness galaxies with
Legacy DR9/DR10/DR11 FITS data on Windows (other platforms remain desirable).
The interface must help a researcher locate data, distinguish exploration
from calibrated input, inspect scientific evidence, and reproduce results.
Clarity and reversibility outrank decoration.

## Existing supported Survey Explorer journey
1. Enter a target in ICRS decimal RA/Dec and a survey-search field width
   in arcmin. Select releases explicitly.
2. Preview the field in the **hosted** Legacy Viewer (image / model /
   residual); treat its layer and zoom as navigation only.
3. Discover release-covered intersecting bricks; compare observed
   release/region and summary NEXP while respecting caveats.
4. Select an individual science quartet (IMAGE, INVVAR, NEXP, MASKBITS)
   or a controlled larger archive selection; inspect file-level identity.
5. Probe candidate URLs (HEAD evidence is not file integrity) and confirm
   local output directory, count and size uncertainty before transfers.
6. Download original FITS and preserve source/report provenance.
7. Inspect local pixel-level coverage with explicit object/sky screening
   parameters; mark output as diagnostic, not adopted photometry.

## Target workspaces (capability-gated)
- **Survey Explorer**: implemented explorer flow, to be redesigned first.
- **Data & Coverage**: valid FITS, WCS, exposure/validity maps and diagnostics;
  extend only with independently validated scientific implementations.
- **Scene & Masks**: *future*: neighbor masks and provenance-aware overrides.
- **Background**: *future/independent science PR*: background candidates,
  fit/holdout evidence and uncertainty.
- **Photometry**: *future*: fixed and, eventually, justified variable
  isophotal models, radial profiles and curves of growth.
- **Results & QA**: *future*: explicit validation level, uncertainties,
  provenance and reproducible exports.

Do not render speculative operations as enabled or science-qualified.
An unsupported workspace should either be absent or visibly labeled
"planned" with no deceptive action button.

## Interaction principles
- Prioritize a large canvas for image/profile inspection with stable
  celestial coordinates and unit labels.
- Use progressive disclosure: selection, verification, download and
  diagnostics must remain discoverable without occupying the whole screen.
- Preserve selection semantics across filters. Never mistake a hidden
  selected product for a deselected one.
- The user should always see: selected target, release/band, operation
  state, evidence class, and whether data are remote preview vs local FITS.
- Distinguish no catalog match, remote Viewer failure, missing archive file,
  incomplete local quartet, and science-quality warning.
- Provide safe retries and explicit cancellation **when actually supported**.
  Do not invent progress or a cancel button for an uncancellable worker.

## Out of scope for UI-only changes
Altering photometry mathematics, FITS values, WCS transforms, coverage
decision logic, automatic science approval, or cross-release deduplication.
