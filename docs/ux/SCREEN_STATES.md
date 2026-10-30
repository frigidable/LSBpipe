# Survey Explorer screenshot and scenario matrix

The matrix is an **acceptance plan**, not a claim that all states are
automatically captured today. `scripts/capture_gui_baseline.py`
now captures S0–S3 in deterministic offline mode.

| ID | State | Required evidence | Current automation |
| --- | --- | --- | --- |
| S0 | Empty / initial launch | Search and target controls, clear remote preview disclaimer | Offline capture |
| S1 | Catalog results | Brick identities, release origin, summary-NEXP caveats | Synthetic offline capture |
| S2 | Filtered products | Selected vs visible count, persistent hidden selection | Synthetic offline capture |
| S3 | Science quartet selected | Exactly 4 intended product identities | Synthetic offline capture |
| S4 | HEAD probe running | Distinguishable pending state, no fake size | Planned fixture |
| S5 | Archive evidence mixed | HTTP_OK/MISSING/UNVERIFIED visibly distinct | Planned fixture |
| S6 | Download confirmation | Total, missing, unknown bytes, destination | Planned fixture |
| S7 | Download partial failure | Recoverable files, failure report, no false success | Planned fixture |
| S8 | Local coverage diagnostic | Explicit sampled region, units, DIAGNOSTIC_NOT_ADOPTED | Planned fixture |
| S9 | Remote viewer error | Local controls preserved, external-browser fallback | Planned fixture |
| S10 | Invalid input / no release | Useful validation without modified science data | Planned fixture |

For every supported state, capture at 1366x768, 1600x900 and
1920x1080 (logical pixels) on Windows; additionally check 125%/150%
display scaling on a real Windows session when available.

## P0 capture semantics
`capture_gui_baseline.py` captures S0–S3, disables hosted Viewer navigation by
temporarily substituting the WebEngine widget with a blank QWidget\nand overriding `show_viewer` **only in the capture process**.
It does NOT download data, establish internet or viewer availability,
or display genuine survey tiles. The resulting offline placeholder viewport
must be labeled as an offline placeholder. No data assertions are inferred
from this screenshot.

Each screenshot has an adjacent JSON record describing:
size, captured application state, placeholder status, window bounds
and selected widget bounds. Automated assertions at this stage check
only screenshot validity, file presence and geometry serialization.

## Future fixture requirements
Use deterministic fake catalog, HTTP and local FITS outcomes through
isolated adapters instead of live archive calls. Test keyboard access,
scroll reachability, state retention, cancellation semantics and errors.
Do not record real research object FITS in public test artifacts unless
explicitly licensed and stripped of private paths.

## Review template
For every screenshot set, record confirmed problems with:
affected state and screen size; screenshot file; reproduction steps;
scientific consequence; priority; repair and verification evidence.
Do not assign aesthetic scores to unseen screenshots.

## Updated offline gates (after image-first redesign)

For each S0–S3 capture, actual PNG dimensions must exactly match the
requested logical window size. The available preview pane width must be
at least 55% of the requested width. When the data dock is open, the
primary download action must remain within the visible window; scrolling
of individual file rows is permitted. **Tests must fail**, not silently
resize the window to satisfy widget minimum sizes.

The capture attempts to register installed Windows system fonts so its
PNG text is legible. Font provenance is recorded in JSON. It does not
commit or distribute font assets. Offline Viewer remains a blank area
and must never be interpreted as remotely loaded sky imagery.
