# LSBpipe — Codex repository guidance

LSBpipe is a scientific photometry research codebase for surface-brightness and integrated photometry of galaxies, especially low-surface-brightness galaxies in DESI Legacy Surveys. Scientific correctness and reproducibility take priority over adding features or GUI polish.

## Baseline

- `main` is a reviewed, reproducible baseline. Start new work on feature branches; do not silently change science definitions or rewrite established commits/tags.
- Before editing, inspect `src/lsbpipe/`, `tests/`, `docs/` and `pyproject.toml`. Run `python -m pytest -q` and preserve existing behavior unless a change is explicitly justified and tested.
- The v0.2 baseline passed 56 tests on Windows with Astropy and Photutils; that does not establish accuracy on real Legacy galaxy images.

## Scientific invariants

- Preserve original images and independent input maps unchanged; all residual-background and contamination models are derived products.
- Preserve raw *signed* pixel intensities, annular fluxes and curves of growth. Negative values are valid measurements. Never silently enforce monotonicity or impute a masked region.
- Separate direct observed flux, mask-reconstructed flux, asymptotic extrapolation and parametric-model flux. Never present them as equivalent.
- Treat `TARGET_PROTECT` as a science-context hypothesis. It cannot override invalid input, no coverage, saturation or real foreground contamination.
- Native Legacy `MASKBITS` are semantic and band-dependent. Do not exclude every nonzero bit automatically.
- Cross-image arrays must match in WCS/grid identity, not only pixel shape.
- Use a named sky PA convention (north through east); transform to pixel coordinates through WCS with explicit tests.
- For pre-sky-subtracted Legacy coadds, model *residual* sky only. It is not possible to promise a recovery of large diffuse structures already removed upstream.
- Keep sky estimation, candidate validation and science adoption logically distinct. A successful fit or minimum RMS is not itself a successful science measurement.

## Code and tests

- Keep numerical core independent of Qt, GUI or cloud services.
- Prefer explicit typed data and small pure functions. Make numerical units and array conventions visible in code and results.
- Test synthetic ground truth, corrupt and missing data, masked sectors, rotated/reflected WCS and reproducibility.
- No production network calls in unit tests. Do not add real FITS/coadds, local caches, credentials, temporary outputs or oversized results to Git.
- Document assumptions, scientific limitations, and all newly introduced QA thresholds. Do not invent calibrated acceptance thresholds.
- Report exactly which tests were run. Treat passing synthetic tests as *engineering verification*, not validation for scientific publication.
