# LSBpipe GUI — evidence-based engineering audit and next gates

Scope: `feature/science-qa-gui` / draft PR #6, based on original-FITS
pilot PR #5; inspected 2026-10-31 at `68b99128`.

**Verification context:** the latest `68b99128` Science-Core workflow
and Windows offscreen UI-capture workflow concluded SUCCESS. Synthetic
GUI states are valuable regression evidence, but no user-machine
review of the current interactive GUI against the real DR9 FITS
has occurred. Do not claim publication-quality photometry.

## P0 — correctness, traceability and interactive responsiveness

### P0.1 Immutable science context and stale-result prevention

Observed: `SurveyExplorer._science_qa_ready()` checks selected
release/region/brick/band, but not source root, full ICRS geometry,
radii or a unique run identity. The FITS Explorer checks ICRS and
selection but not the directory of original files. The right-side
directory fields do not invalidate previously saved output on edit.
Action: make a typed immutable `ScienceContext` holding release,
region, brick, band, RA/Dec/field, aperture radii, exact quartet
paths/metadata and data-root identity. Assign a monotonically
increasing request token. Validate the complete context at both
worker submission and result delivery, and discard late outputs
after any context change. Show concise provenance at all times.
Acceptance: tests change every single field during an active
worker task; no stale result, bitmap or saved-report button survives.

### P0.2 Non-overwriting audit artifacts

Observed: `science_workflows._file_stem()` includes release/region/
brick/band and the requested sky square, but **omits the target
and sky-annulus radii, stretch and mask-display settings**. The
`masks` and `figure` actions can therefore overwrite earlier outputs
for the same target with different settings. Outputs are placed
inside the downloader's `data/` subtree.
Action: write every run to a separate versioned result directory
outside the original quartet, using a manifest containing input
SHA-256 (or verified receipt hashes), exact parameters, source
units/HDU/WCS, code version and UTC timestamp or run id. Save
JSON and PNG atomically; manifest filenames link back to inputs.
Acceptance: two differing QA parameter sets produce two distinct
immutable result directories; recreating one deterministically
matches measurement counts, and files containing input FITS
are not written to.

### P0.3 Never equate invisible data with zero science signal

Observed: in `fits_raster._stretch()`, NaN IMAGE, zero/negative
INVVAR and NEXP zero can all be visually black. The UI currently
renders DISPLAY RGB without a dedicated visual invalid/no-data
state or a quantitative colorbar. `QGraphicsView` is configured
with smooth-pixmap transformations, inappropriate for inspection
of exact pixel boundaries unless the viewer makes the mode clear.
Action: checkerboard or distinctive alpha hatch for nonfinite
and unavailable values; show masks with a customizable alpha,
clearly separate invalidity from warning bits. Add a switch
between nearest-neighbor (default for pixel QA) and smooth
interpolation (preview only). Provide display vmin/vmax, asinh/
linear/log choices as appropriate, percentile range, colorbar
and BUNIT labels. Never infer instrumental depth from a display
stretch. WCS pixel inspector must report original unmodified values.
Acceptance: synthetic signed/NaN/zero cases are visually distinct
and pixel-accurate at every zoom level.

### P0.4 Bounded memory and nonblocking rendering

Observed: `fits_raster.prepare_fits_explorer()` returns **four
full cutout arrays and four three-channel RGB previews**, while
`FITSExplorer._show_layer()` recomputes faint stretch, Qt pixmaps
and RGBA overlays on the GUI thread. For a 5' field and ~0.262
arcsec/pixel with 1.6x padding, the cutout is ~1,833 x 1,833;
duplicate arrays, display caches and temporary float64 data may
consume appreciable memory and cause stalls on modest laptops.
Action: explicit cache budget, render-generation token, lazy
on-demand layer generation, background downsampled previews,
one full-resolution view or tiled LRU cache. Keep authoritative
raw arrays read-only; throttle WCS hover and permit cancel.
Acceptance: bounded peak memory and responsive zoom, pan, layer
switching and repeated mask toggling at 1366, 1920 and 4K DPI.

## P1 — user journey and robust science inspection

### P1.1 A coherent workflow instead of a long action-button column

Observed: a narrow right QScrollArea currently holds data roots,
four earlier actions plus new interactive exploration, two
additional North/South path fields and a growing text log.
Action: progressive panels `Data` → `Inspect` → `Masks` →
`Compare` → later `Sky`; a persistent compact target/brick/band
context header; per-step status (Not run/Running/Passed/Warning/
Failed); and a run-history list. Keep a large central canvas,
shortcuts, readable labels and keyboard navigation.
Acceptance: critical steps discoverable at 1366x768 without
scrolling through unrelated comparison controls; no accidental
cross-region analysis.

### P1.2 Local overlays with scientific semantics

Need toggles for SATUR and ALLMASK separately; Gaia BRIGHT and
MEDIUM, GALAXY/SGA, NPRIMARY, CLUSTER, BAILOUT, finite IMAGE,
INVVAR/NEXP coverage. Provide an inspectable, cumulative policy
overlay: what is `hard excluded`, `source warning`, or `candidate
sky`, rather than one permanent exclusion mask. Add a legend
that explains combined bit priority, world-coordinate cursor
and optional crosshair for another pane.
Acceptance: overlay counts match the JSON mask census on the
identical sky-selected footprint; SGA GALAXY does not silently
remove the target flux.

### P1.3 Realistic and transparent cross-release controls

User already demonstrated the same DR9 sky field has a much
stronger South than North original quartet. Add a native paired
compare view showing joint-validity maps, NEXP differences,
SATUR/ALLMASK intersections and `no independent exposures`
warning. Scope comparisons to truly matching WCS and explicitly
display any absence/partial footprint. Later implement
multi-brick/three-band context, not automatic science mosaicking.
Acceptance: original test field reproduces both-valid 1,286,594,
South-only 22,122, North-only 0, both-invalid 2,309; WCS mismatch
refuses comparison.

### P1.4 Evidence and release-quality tests

Extend current Windows 1366/1600/1920 synthetic screenshots with
high-DPI/4K, keyboard navigation, resized docks, warning/error
states, and contrast/mask toggles. Add a small scientifically
sanitized realistic FITS fixture with WCS, units, mask overlap
and negative sky; retain original file checksums separately.
Use Golden numerical assertions and measured performance budgets,
not only visual screenshot presence. Avoid bundling the original
full-size third-party coadds into the Git repository.
Acceptance: all offline test matrices pass and user verifies
real Windows FITS interactively before merging PR #6.

## P2 — stage after sky and PSF methodology is scientifically frozen

Build a non-destructive foreground/neighbor mask editor with
versioning, undo/redo and provenance; a source segmentation
review layer; bright-star wings and PSF diagnostics; multi-window
sky modeling with residual surfaces and stability probes;
radial isophotes, surface-brightness/color profiles,
curve of growth, and uncertainty visualizations including
coadd noise covariance. All quantitative profiles must be
verified by injection/recovery and cross-band controls before
the GUI offers 'science-ready' output.

## Recommended order

First P0.1/P0.2, then P0.3/P0.4. After that P1.1 and P1.2;
validate the existing DR9 South/North dataset within a combined
GUI session, and only then expand P2 photometric capabilities.
Stop adding cosmetic controls until scientific input provenance
and rendering performance are demonstrably reliable. Keep
both PR #5 and PR #6 as Draft; do not modify `main`.
