# Photometric core architecture — design baseline P1.1

> **Stage-order update (P1.3, 2026-11-03):** the logical workflow is now
> Data Preparation → Scene Initialization → Preliminary Sky →
> PSF Reconstruction → Final Scene & Smart Masks → Sky Reconstruction →
> Conditional Refinement → Galaxy Photometry & QA. See
> `PSF_SCENE_SKY_ORDER_P13.md` for authoritative ordering, PSF reuse
> and validation requirements. The milestone labels below are historical
> and must not be interpreted as executable order. P1.1 remains a
> non-measuring preparation screen.

Date: 2026-11-03. Status: scientific architecture plus a **non-measuring**
project-bound GUI preparation screen. This document is a design contract,
not evidence that the following numerical methods exist or are validated.

## 1. Navigation and authority boundaries

Galaxy Projects -> Galaxy Workspace -> Photometry Workspace.

- **Projects** owns the object identity and library entry, not a photometric
  result or a FITS copy.
- **Workspace** owns the project state, selected observational sources,
  catalogues, data manager, source QA, frozen analysis configurations, and
  history. Existing survey/FITS exploration remains available.
- **Photometry** is a dedicated task surface. Its entry point requires an
  object project. Navigation must not issue network requests, download files,
  adopt previous QA, or write results.
- External catalogues (SIMBAD/NED/Tractor) provide literature/comparison
  values, never an adopted result or hidden prior on the fit.
- Scientific results belong to a unique immutable **analysis run**. A galaxy
  can have many runs with distinct releases, sky models, masks and algorithms.

P1.1 is a preparation surface: reads the *saved* project selection and checks
whether four native optical paths exist. This is neither source integrity nor
FITS/WCS validation. It never reports `SCIENCE_READY`, displays invented
fluxes, or launches analysis. A complete native quartet is necessary for
the first intended coadd path, but not sufficient for photometry.

## 2. Scientific invariants

1. Preserve original IMAGE/INVVAR/NEXP/MASKBITS bytes and pixel coordinates.
   Use read-only references with file hash / survey identities in run receipts.
2. All photometric source measurements are signed **linear fluxes** first.
   Never log-transform non-positive fluxes; maintain flux likelihoods and
   limits distinct from unavailable measurements.
3. Make explicit all units, ICRS/WCS, calibration source, filter transmission,
   AB/Vega convention, extinction, pixel solid angle and coordinate origin.
4. The normal Legacy optical coadd IMAGE is nMgy/native pixel;
   `m_AB = 22.5 - 2.5 log10(F_nMgy)` applies to positive integrated flux
   *after* the justified background/contamination treatment. Surface
   brightness needs an explicit area correction and pixel geometry.
5. The coadd INVVAR is **diagonal evidence**, not an aperture flux
   covariance matrix. Resampling, background variation and masking can
   induce correlated errors. NEXP is a coverage/count map, not inverse noise.
6. MASKBITS is an unsigned semantic bit field, not a reject-all-nonzero mask.
   Preserve named invalid/contaminant/target-protected/user-exclusion layers.
7. Keep observed/processed/model/residual and neighbouring-source models
   separate; no silent subtraction, interpolation, PSF matching or mosaicking.
8. Overlapping bricks/releases and CCDs may reuse original exposures.
   Record shared origin and do not treat such products as independent.
9. A locally fitted residual sky cannot generally recover light removed
   upstream by the survey reduction; report identifiability limits.
10. Any automatic selection of method/parameters must be reviewable,
    versioned and scientifically justified; user overrides are recorded.

## 3. Core data flow

```text
ObjectProject + Dataset Registry
          |
Input Contract: NativeBandFrame / future CCDFrame
          |
Scene Definition: region, target center, neighbors, mask policy
          |
Sky / PSF / Noise preparation (independent versioned artifacts)
          |
   +------+------+
   |             |
Isophotal       Parametric forward fitting
non-parametric  galaxy + sky + contaminant sources
   |             |
   +------+------+
          |
Joint error model + identifiability + injection/recovery tests
          |
PhotometryRun -> ScientificMeasurements -> QA -> Export
          |
Downstream: colors, SBF eligibility, distances, populations, UDG
```

**Coadd-only (first deliverable):** independent image per
release/region/brick/band. Multi-brick analysis must wait for a validated
mosaic or sky-domain joint-fitting policy. Do not combine duplicate pixels
or average independent-looking releases.

**Hybrid (target architecture):** coadd provides detection and initialization.
Independent CCD frames then constrain a common sky-coordinate galaxy model
while retaining exposure-specific PSF, zero point, residual background,
valid-pixel map, calibration and noise. The CCD likelihood should operate
on native pixels when possible; image reprojection is an explicit derivative.
Coadd and CCD fits are not statistically independent evidence if coadd
contains those CCDs.

## 4. Reconstruction and photometric estimators

### 4.1 Observation forward model

Conceptually for calibrated exposure e and band b:

```text
y[e,b] = a[e,b] * W[e,b]((G[b] + N[b]) convolved P[e,b])
       + B[e,b] + noise[e,b]
```

G is the target's astrophysical light; N its neighbours; P the PSF
(including wings as appropriate); W the WCS/pixel integration operator;
B the residual instrumental/sky contribution. A cirrus component may
be explicitly modelled only with defensible constraints. Exact operator
order, pixel response, units and likelihood depend on the camera/product.

For Gaussian errors with known covariance `C`:
`-2 log L = r.T @ C^-1 @ r + log|C| + constant`.
The practical implementation may use empirically calibrated local
correlation kernels, blank apertures or structured operators rather
than an infeasible dense global covariance. These approximations
need recovery/coverage testing.

### 4.2 Sky & contamination

- Candidate constant/plane/low-order smooth sky models, with independent
  blank regions, target protection and masked sources.
- Explicitly fit or marginalize sky along with extended galaxy light.
  Require a held-out sky diagnostic, robust spread across plausible models
  and tests of degeneracy with effective radius and extrapolated flux.
- Extended PSF wings and halos of bright stars may require a larger field
  than the galaxy measurement window.
- A flexible spline that reproduces observed pixels is **not** proof that
  it preserved the galaxy outskirts.

### 4.3 Non-parametric photometry (baseline)

- WCS-registered elliptical geometry: center, q=b/a, PA (with documented
  on-sky convention), semimajor axis and annular boundaries in arcsec.
- First fixed-geometry, band-specific/forced-geometry aperture estimates;
  subsequently iterative free isophotes with explicit stopping criteria.
- Use exact or sufficiently converged subpixel fractional integration
  for masks and elliptical annuli, preserving native flux.
- Per-annulus *signed* flux, effective unmasked area, local surface
  brightness when positive, intensity uncertainty, flags, valid fraction.
- Curves of growth are accumulated from **measured** aperture fluxes;
  do not enforce monotonicity by modifying noisy points.
- Define a measured truncation radius from signal/background evidence;
  record extrapolated model totals separately.

Useful references:
- photutils aperture methods:
  https://docs.astropy.org/projects/photutils/en/stable/user_guide/aperture.html
- photutils isophotes:
  https://docs.astropy.org/projects/photutils/en/stable/user_guide/isophote.html

### 4.4 Parametric photometry

Initial target model: single Sérsic, convolved with an empirical PSF;
fit galaxy plus sky and selected contaminants. Store parameter covariances
and model-extrapolation sensitivity. Fit in native flux and pixels.
Alternative profiles/nuclei/disk breaks only after a model-selection and
identifiability check. A success flag from an optimizer is not enough.

Reuse and benchmark third-party solvers where scientifically suitable.
AstroPhot already supports joint multi-image modelling, PSFs, neighbours
and joint parameter covariance; Desidera should not build a competing
general optimizer without demonstrating a concrete unmet requirement.
https://academic.oup.com/mnras/article/525/4/6377/7243402
https://astrophot.readthedocs.io/en/v0.17.2/tutorials/JointModels.html

### 4.5 Multi-band analysis

Preserve individual filter-system response (BASS/MzLS vs DECam).
Fit or measure each band under its PSF and correct spatial footprint;
colors need matched or forward-model-consistent apertures.
Flux covariance across bands includes shared geometry/sky priors where
applicable. A missing or failed z/i band must not be silently imputed.

### 4.6 SBF and physical inference

Downstream only. SBF requires independently tested PSF, spatial noise,
background/masks, validated passband/population calibration and recovery
tests. Detection of SBF power does not imply a valid distance.
Padova SSP/CSP, WISE, HI and distance-based physical parameters consume
photometric products *with correlations and provenance*, never replace
the core measurement or its QA.

## 5. Typed scientific objects (planned)

- `ObservationSelection`: object UUID, source IDs, release, region, brick,
  band, exposure identities, instrument, archive path, source hashes.
- `PreparedScene`: source WCS, ROI, protected target, mask layers,
  neighbouring sources and explicit versioned footprint.
- `SkySolution`: model coefficients, units, fit/holdout regions, priors,
  posterior/covariance, residual maps, validation state.
- `PSFSolution`: empirical model/position, normalization, wings,
  residuals, source-star evidence, validation state.
- `NoiseSolution`: diagonal weights, effective correlated-noise model,
  scale-dependent blank-sky sensitivity with validation evidence.
- `PhotometryConfiguration`: frozen data, method and all settings
  with a digest. No hidden mutable defaults in published science.
- `PhotometryRun`: immutable input dependency IDs, code version, full
  environment, seeds, completion manifest, artifacts and QA.
- `PhotometricMeasurement`: method, band, aperture/model definition,
  signed flux, uncertainty and covariance link, units, effective area,
  optional magnitude, masks, provenance and scientific status.

These are **new conceptual interfaces**, not classes claimed to exist in P1.1.
Reuse P0.5 `NativeBandFrame` and P0.3 run contracts rather than building
a second registry or overwriting their schema.

## 6. Eligibility, scientific statuses and adoption

Input readiness and scientific acceptance must remain distinct:
`NO_SELECTED_DATASET`, `DATA_ROOT_UNAVAILABLE`,
`INCOMPLETE_QUARTET`, `FILES_PRESENT_UNVERIFIED` describe *file paths*,
not photometry. Subsequent validation should add explicit states for WCS,
units, masks, sky, PSF, noise and geometric coverage.

Measurement results should support `VALID`, `LIMIT`,
`UNDERCONSTRAINED`, `FAILED_QA` and `UNAVAILABLE` with a versioned
definition of their criteria. Exploratory products must retain existing
`DIAGNOSTIC_NOT_ADOPTED` / `EXPERIMENTAL_NOT_ADOPTED` labels until a
separately reviewed scientific adoption protocol is implemented.

Require flags for background–galaxy degeneracy, unmodelled wings/neighbor
contamination, incomplete footprint, inconsistent band calibration and
posterior/prior domination. No single photometric quality score may conceal
a failed blocking gate.

## 7. Screen contract and intended UX

P1.1 shows a distinct Photometry workspace only inside an object project,
with return navigation and Data Manager access. On entry it reads the saved
selection *offline*, showing the four expected input file paths and an
unambiguous non-adopted status. Any input existence check is read-only.

The eventual screen should use:
- Left: pipeline stage navigation and progress, clear blocked conditions.
- Center: large local FITS canvas (image/model/residual/masks with WCS),
  plots and cross-band controls; no remote Viewer pixels in science.
- Right: contextual inputs: region, source/mask editor, sky models, PSF,
  isophote geometry, model selection, nuisance parameters.
- Bottom: immutable run selector, stage-level error budget, QA evidence,
  audit logs, reproducible export.

A real-time preview can display exploratory derivatives but must not
write over source FITS. A new accepted run must be explicit, attributable
and reproducible, with previous runs remaining accessible.

## 8. Release sequence and acceptance gates

| Stage | Deliverable | Blocking scientific evidence |
| --- | --- | --- |
| P1.1 | Workspace navigation, read-only data snapshot, design contract | Offline GUI path, missing-file cases, no network or FITS writes |
| P1.2 | Native ROI, region editor, evidence-preserving masks | WCS/area accuracy and mask semantics synthetic + real |
| P1.3 | Residual sky candidate engine | Injected diffuse light, holdout sky, sky/galaxy degeneracy |
| P1.4 | Coadd fixed-ellipse flux/profile engine | Analytic images, signed flux, fractional masks, blank-aperture tests |
| P1.5 | Free isophotes + growth curves | Ellipse recovery and failure cases, measured vs extrapolated flux |
| P1.6 | PSF and coadd noise model | Empirical stellar PSF, wings, scale-dependent correlation |
| P1.7 | Sérsic forward fit | Model recovery, residuals, structural parameter coverage |
| P1.8 | Multi-band colors and QA | Filter compatibility, PSF effects, shared uncertainty |
| P1.9 | End-to-end runs, provenance, reporting | Immutable dependency DAG, 68/95% coverage, science gate |
| H1 | CCD exposure adapter and joint fitting | Shared-CCD lineage, native-pixel model, compare with AstroPhot |
| S1 | Experimental SBF | Masked power spectra, correlated noise, injection/TRGB tests |

Do not claim completion from synthetic-only tests; require real-FITS
acceptance and independent benchmarking before publication-grade status.
For initial *photometry* pilots use Leo P first, followed by available UGC 2162 and a diverse control set
(extended faint dwarf, compact dwarf, sky-crowded/star-contaminated field,
brick boundary, known bright comparison galaxy). Leo P can stress-test
resolved stars and irregular morphology but is not a universal Sérsic
ground truth.

**Validation questions:** bias in total magnitude, R_e, n, q, sky and
outer-profile slopes; repeatability under sky/mask changes; empirical
interval coverage; recovery across surface brightness, size, PSF and
crowding; and whether hybrid genuinely improves over existing solvers.
A claim of methodological superiority is conditional on those tests.
