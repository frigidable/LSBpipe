# P1.3 — PSF-first scene, sky and photometry architecture

Date: 2026-11-03. Status: **approved architectural requirements expressed as
a design specification, not implementation or scientific validation**.
This supersedes the *logical stage ordering* in P1.1 and P1.2a, but
does not change the numerical claims, saved science statuses or existing
source/FITS handling. Base: P1.1 -> P1.2a -> P1.2c stacked draft PRs.

This document incorporates two detailed researcher-supplied PSF/methodology
notes (NGC2146sat empirical-PSF approach and the corrected PSF/scene/sky
sequence). The source notes are design input. The official documentation
links at the end independently support the specific survey/library behavior.

## 0. Non-negotiable scientific premise

The PSF, source contamination and residual galaxy sky are **coupled**
inverse problems. An apparently successful optimizer or a visually smooth
image cannot establish that a faint outer galaxy profile is preserved.

- A final contaminant mask needs information from an appropriately tested PSF.
- A reliable PSF needs selected stars, valid original pixels and a local sky
  estimate on their cutouts, not a scientifically final galaxy sky model.
- A final galaxy sky needs a PSF-aware contaminant model and protected
  faint galactic light. During structural photometry galaxy and residual
  sky parameters can still require **joint fitting**.
- A preliminary isophotal or interactive user mask remains useful before
  the PSF has been built, but must be identified as provisional.
- Avoid a literal recursive execution graph. Scientific refinement
  creates versioned artifacts and selectively invalidates descendants.

## 1. Adopted order: eight logical stages, not eight separate GUI screens

| # | Stage | Artifacts and meaning | Prohibited automatic claim |
|---|---|---|---|
| 01 | Data Preparation | Native FITS quartet/CCD, quality masks, WCS, band, instrument, source fingerprints | File presence means accepted science |
| 02 | Scene Initialization | Scene_v0, tentative SourceCatalog_v0, rough target protection, preliminary segmentation, candidate PSF stars | Definitive target/source classification |
| 03 | Preliminary Sky | Initial detection noise/sky; distinct PSF-star cutout backgrounds after star selection | Final faint-galaxy background |
| 04 | PSF Reconstruction & Validation | Per-band empirical PSF_v0; optional CCD PSFEx/independent coadd reconstruction; coverage/held-out QA | Locally valid PSF from global stars without test |
| 05 | Scene & Smart Masks | Scene_v1, PSF-aware contaminants, manual shared ICRS anchors with band-local masks, neighbor models | “Best mask” based only on source threshold |
| 06 | Sky Reconstruction | Residual Sky_v1 and sky-model sensitivity/holdout evidence | Upstream removed galaxy halo recovered |
| 07 | Refinement Loop | Conditional assessment and revisions PSF_vN/Scene_vN/Sky_vN; selective reexecution | Optimization converged implies scientific precision |
| 08 | Galaxy Photometry & QA | Fixed/free isophotes, parametric forward models, colors, uncertainties and adoption gate | Final result with unchecked PSF/sky/masks |

The UI maintains a single Photometry Workspace and supports an
interactive preliminary/manual mask editor from stage 02. PSF-informed
mask proposals are enabled only when the relevant per-band PSF evidence
is available at stage 05. Stage 04 belongs **before** stage 05.
SBF-grade PSF remains a downstream specialized science-gated extension.

## 2. Define boundaries precisely

### 2.1. Scene Initialization (Scene_v0)

Inputs: immutable observational source references, WCS, policy-specific
native validity masks and user/project ICRS target location. Local robust
noise can be iterated internally with tentative source detection.

Outputs:
- source candidates with explicit **uncertain classification**
  (star/galaxy/unknown) and reasons/evidence;
- rough protected target footprint, preferably larger than the immediate
  visible bright component so genuine diffuse outskirts are not fit away;
- basic contaminant/bright-source and stellar-training masks by purpose;
- candidate positions for PSF construction, with cuts not predicated on
  having already removed or masked *all* useful stars.

Do not try to complete a PSF-aware scene here. A preliminary user seed
in Smart Mask can mark a suspect source, but a connected threshold
from the coadd does not certify its extended wings.

### 2.2. Preliminary Sky (Sky_detection and Sky_star_cutouts)

Keep these as **different data products** with different footprints
and dependency identifiers. Local star-cutout background is usually
constant or low-order over a small uncontaminated field, but must exclude
neighbours and must not absorb significant stellar PSF wings.

A prelim detection sky can inform star selection. The actual
`EPSFBuilder` input cutouts require background subtraction *before*
PSF reconstruction; photutils does not estimate this inside the builder.

The coadd has already undergone survey processing. In DR9+ the
Community Pipeline and Legacypipe perform background treatments,
so Desidera analyzes **residual** backgrounds and potential oversubtraction.
A locally fitted sky cannot restore unresolved signal removed upstream.

### 2.3. PSF Reconstruction & Validation (PSF_v0)

Use the empirical star-stack/ePSF approach previously employed for
NGC2146sat in DR9 North as the baseline coadd method. It must be
possible to process g, r, z separately (i only where actually available).

**StarSelector** should use Gaia (optional evidence, not a sole gate)
and local image morphology, S/N and saturation, neighbor isolation,
sky quality and spatial distribution. Selection criteria are scale-
dependent: a star suitable for a compact core cannot be assumed to
measure PSF wings out to tens of arcsec.

**EmpiricalPSFBuilder**:
- use individual background-subtracted stellar cutouts;
- refine subpixel centers and flux normalization iteratively;
- use robust stack/fit with documented outlier reasons;
- choose oversampling adaptively, not always 4x; retain native pixel
  integration/normalization conventions;
- preserve absolute/encircled-flux normalization and report unmeasured
  power beyond the empirically constrained PSF extent.

**Spatial representativity** must be measured, not assumed:
- Global empirical: stars across an observational brick/coadd, a sound
  baseline if holdout-star residuals show sufficient spatial stability;
- Local empirical: a model near the target only if enough clean local
  stars support lower expected predictive error;
- Spatial model: e.g. smooth basis/low-dimensional coefficients varying
  with detector/sky position, complexity limited by quality and
  geographical spread of training stars;
- Comparison on independent holdout stars/leave-one-out, including
  ellipticity, residual structure, radial profile, wings, systematic
  flux errors and evidence for position dependence.
- If spatial interpolation has weak support, do not silently force it.

Three meaningful PSF scales must be represented, with actual
boundaries established empirically for instrument/seeing:
1. Core: FWHM, ellipticity, asymmetry, accurate subpixel response.
2. Intermediate: a few-to-tens of arcsec, star contamination near
   the galaxy, multi-band matching, compact structures.
3. Extended/wide-angle: far wings, scattering and star halos, using
   unsaturated outer pixels and independent normalization constraints.

A complete model `P(x,y; position, band, observation)` should maintain
flux normalization on a clearly specified finite region and flag
unknown flux beyond it. The extended PSF can retain asymmetric
diffraction/reflection artifacts; a radial average alone is insufficient
when those residuals affect the target.

#### Survey-specific PSF evidence

- `SurveyPSFAdapter` for separate CCD PSFEx (or compatible source model);
  the actual per-exposure/per-position model should be preferred for
  CCD-forward modelling in Hybrid Analysis, **with an independent
  stellar validation**.
- Coadd PSF is affected by underlying CCD PSFs, weights, resampling,
  coverage and source masks. An effective coadd PSF reconstructed from
  CCDs is an *approximation* until checked on coadd stars.
- Empirical coadd PSF and PSFEx-derived coadd PSF are methodologically
  distinct but **not statistically independent observations**; they
  derive from related imaging. Do not combine them as independent likelihoods.
- DR9 North g/r observations are BASS/90Prime and z MzLS/Mosaic-3;
  DECam-specific bright-star halo models must **never** be copied
  directly to North data.
- Starting DR9, Legacy Survey applies an extended-wing bright-star
  subtraction model for DECam. Desidera must model the **remaining
  contamination in the delivered processed image**, not subtract the
  *already corrected* total star halo again.

Products: `StarSelection`, `StarCutoutSky`, `PSFCore`,
`PSFIntermediate`, optional `PSFWings`, `PSFQA`, and an immutable
`PSFModelReceipt`. These are **target interfaces only**. Do not
claim the Python classes or algorithms exist in P1.3.

PSF retrieval should be associated with the **observation dataset**,
instrument, release, band and source hashes, not merely a galaxy
project. Global PSF for the exact same coadd may be reused for several
galaxies only when spatial applicability has been checked at each
target. Local evaluation also depends on position.

### 2.4. Scene & Smart Masks (Scene_v1)

This is where the first PSF-informed mask/refinement engine runs.
- The researcher points at a source once in ICRS; preserve the shared
  source identity across g/r/z while creating **independent** local
  pixels/contours and uncertainty in each band.
- For ordinary isolated stars, an anchored connected-isophote mask is
  a quick preview. It must not masquerade as a validated
  full-wing mask or post-PSF `optimal` solution.
- For bright stars, use fitted star amplitude times evaluated PSF and
  choose a masking boundary from predicted impact on recovered galaxy
  sky/flux/profile, with a wing/halo model uncertainty envelope.
- If a bright/saturated star is ambiguous or PSF wings are unresolved,
  use a conservative mask and explicit `UNCONSTRAINED_WINGS` flag.
- For blends overlapping legitimate galaxy light, compare masking,
  joint star+galaxy+sky modelling and explicitly validated subtraction
  alternatives. A smooth residual is not a guarantee of unbiased galaxy
  morphology.
- Make each proposal visible, reversible and reviewable; keep
  native hard masks immutable, retain manual edits and per-band
  exceptions, and invalidate downstream products after accepted edits.

### 2.5. Sky Reconstruction (Sky_v1)

Fit the remaining sky around the protected, PSF-aware source model.
Compare plausible flat/gradient/smooth templates using disjoint
training and holdout sky regions and assess degeneracy with large
effective radius/extrapolated galaxy flux. Do not make low-order
spline flexibility a substitute for identifiability. Output includes
per-band residual maps, background-parameter uncertainties, correlated
noise and systematics due to star wings and survey oversubtraction.

### 2.6. Refinement and Photometry

Run additional loops **only if evidence demands it**:
- previously used PSF star found blended by Scene_v1;
- significant control-star PSF residuals/spatial mismatch;
- alternative PSF wings change the adopted star masks or galaxy halo;
- sky-model uncertainty changes target structural inference.

A newly estimated PSF_v1 can trigger new Scene_v2 and Sky_v2, while
keeping PSF_v0/Sky_v1 available as historical artifacts. Stopping
criteria include holdout residuals, encircled-energy/radial profiles,
mask stability, sky quality and downstream galaxy parameters—not FWHM
alone. Non-identifiability across plausible solutions is an explicit
systematic uncertainty, not silent successful convergence.

The final galaxy fit may jointly refine nuisance sky with target
Sérsic/other models. Coadd inverse variance is not necessarily a
complete independent-pixel likelihood due to resampling correlation.

## 3. PSF product reuse, ownership and reproducibility

**Dataset Registry/Observation Registry owns PSF identity**; each galaxy
project holds a reference to a reviewed dataset-bound PSF realization.

The initial `PSFLineageKey` contract is deliberately a **cache-key
design primitive**, not a validated PSF product, a new parallel
registry, or a proof that a hash was checked against downloaded bytes.
Store:
- exact source product (coadd vs CCD), release, region/brick/exposure,
  instrument/camera and band;
- the original observational source checksum and source evidence;
- PSF star-selection version, cutout-background version, recipe digest,
  applicable spatial scope and position if local;
- stellar candidates used/held out, rejection reasons and residuals;
- center convention, pixel response, normalization extent, flux outside
  region and per-scale model uncertainty;
- independent QA and full kernel/model as a derivative FITS artifact;
- links to original unmodified files and the native observation lineage.

Do not reuse a DECam model on BASS/MzLS, a coadd PSF for a CCD
likelihood, or a global PSF as a local PSF without spatial QA.
PSFEx availability must be verified in each particular release and
exposure; catalogue mentions alone do not prove data were downloaded.

## 4. Versioned dependency graph (minimal reprocessing)

New pure-Python contract `workflow_dependencies.py` enumerates
artifact-stage dependencies for planning, with no jobs executed.

| Changed artifact | Invalidate downstream | Do **not** invalidate solely for this change |
|---|---|---|
| Original FITS/WCS/source identity | Scene_v0, preliminary sky, PSF input/fit, final scene, galaxy sky, photometry | Stored/manual sky-vector shape itself may remain, but must be reprojected/checked |
| Star selection or PSF cutout background | PSF, final scene, galaxy sky, refinement, photometry | Unrelated external catalogue snapshots |
| PSF model or wing QA | Final scene, galaxy sky, refinement, photometry | Original FITS |
| Manual contamination-mask edit | Final scene, galaxy sky, refinement, photometry | PSF star sample/cutout sky if unchanged |
| Final galaxy residual sky | Refinement/photometry | Empirical PSF cutout sky, star sample or PSF by default |

The graph is intentionally conservative; a later real scheduler should
use **exact hashes of the consumed artifacts** so a version change
that does not alter underlying star selection/cutout background need not
rebuild PSF. The source identity graph must stay consistent with the
existing P0.3 run infrastructure, not invent a second results register.

## 5. Explicit acceptance and PSF QA gates

The GUI must expose PSF status per band and task. **No fake completion**.
Critical gates before publishable results:

- sufficiently constrained core and intermediate response, with holdout
  stellar residuals and spatial representativity at the galaxy position;
- if faint outskirts depend on star halo leakage, wing model measured or
  a conservative, propagated uncertainty/masking limitation reported;
- full distinction among unsaturated core, outer-wing fit and residual
  post-survey-halo-subtraction model;
- bootstrap/star-sample sensitivity, optional alternative PSFEx model,
  model-vs-observation residual and encircled energy;
- effect of PSF alternatives on `m_tot`, `R_e`, `n`, `mu_0` and
  outer surface brightness/colour gradients, not just FWHM agreement;
- if no suitable stars, mark PSF unavailable/underconstrained and
  provide an explicit user option to evaluate a documented survey PSF,
  rather than inventing an empirical model.

Per-use policy:
- isophotal photometry: remain on original pixels with documented
  PSF contamination and seeing/ellipticity effects;
- forward Sérsic and joint CCD likelihood: convolve each observation
  with its own appropriately localized PSF;
- matched-aperture colors: PSF matching/homogenization or joint
  multi-band forward models; noise covariance changes after matching;
- faint diffuse features: wide-angle PSF and scattered-light QA;
- SBF: area-matched PSF power-spectrum template combined with masks,
  non-white noise, alternative PSFs and injection/recovery;
- WISE: **different instrument/passband PSFs**, never reuse Legacy PSF.

## 6. GUI contract and acceptance tests

In `Photometry Workspace`, display the ordered high-level stages
without making a placeholder look like a runnable validated algorithm.
At PSF stage show source type, band, model family and completeness.
Future PSF inspector: candidate-star map, train/holdout classes, 2D
kernel, radial log profile, core/intermediate/wings, FWHM/ellipticity,
residuals, spatial stability, dependence on selected stars, local
representativity and export/QA provenance.

Manual mask editor is available for Scene_v0, but a default `Smart
Mask` sourced only from thresholded coadds must clearly say
`PROVISIONAL_PRE_PSF`. After PSF construction a **different**
`PSF_AWARE_REVIEW_REQUIRED` candidate can be offered; both require
explicit researcher acceptance. It is incorrect to hard-code a
fixed angular radius as “optimal” for all bands.

Validate both scientific and UX behavior:
1. Parent offline source/GUI regression suite unchanged;
2. dependency tests show manual-mask/galaxy-sky edits do not
   automatically rebuild a stellar PSF;
3. PSF lineage hash changes for instrument, source, band, star sample,
   cutout sky, recipe, coadd/CCD and local position;
4. no new automatic downloads, source-FITS writes or claimed science
   products from opening the Photometry Workspace;
5. first run the Leo P control on its validated local DR11 South source;
   inspect held-out stars and core/wing QA, then compare methodologically
   with the older NGC2146sat empirical PSFs as a secondary control;
   subsequently UGC 2162 and crowded/bright-star/PSF-sparse fields;
6. use injection-recovery for recovered galaxy magnitude, R_e, n,
   mu_0, color gradients and SBF when appropriate.

## 7. Recommended implementation sequence, revised

- P1.3a: this architecture, conditional dependency contract and GUI
  stage order; no fitted sky/PSF or photometry claimed.
- PSF-0: Leo P is the **first** real control; construct an independent
  empirical coadd PSF per available band, review star selection,
  cutout-sky fit, provenance/QA and two-dimensional diagnostic FITS.
  NGC2146sat remains a **second** comparison baseline.
- P1.2b: research-quality FITS mask editor with provisional masks,
  sky-coordinate linked edits, undo/redo and review.
- PSF-1: local/global/spatial evaluation + CCD PSFEx adapter with
  independent holdouts and camera-specific policy.
- P1.3b: final scene and PSF-aware Smart Mask; weak wing handling,
  model-versus-mask decision diagnostics.
- P1.3c: galaxy residual sky, heldout tests, conditional iterations
  and joint sky–galaxy fits.
- Later P1.4+: isophotal/structural photometry, band matching,
  scientific QA, then specialized SBF implementation.

Ordering in this plan distinguishes a **development sequence** from
the logical **execution order**. In particular, GUI drawing tools
can be developed while PSF-0 is implemented, but they cannot falsely
assert PSF-informed mask optimality before its validation.

## 8. Official methodological references

- Photutils EPSFBuilder requires background-subtracted stellar cutouts:
  https://photutils.readthedocs.io/en/3.1.0/api/photutils.psf.EPSFBuilder.html
- Legacy DR11 PSFEx description (per-CCD spatially varying PSF)
  and previous sky processing:
  https://www.legacysurvey.org/dr11/description/
- Legacy DR9 BASS 90Prime g/r vs MzLS Mosaic-3 z:
  https://www.legacysurvey.org/dr9/description/
- Legacy DR9 DECam bright-star extended-halo subtraction:
  https://www.legacysurvey.org/dr9/psf/

Future review should also examine Anderson & King (2000) ePSF,
AutoProf, elderflower wide-angle PSFs and injection-recovery work
before selecting exact solvers. Bibliographic discussion does **not**
by itself validate a PSF implementation.


## 2026-11-06 amendment: mandatory empirical PSF in every optical science band

**New non-negotiable researcher requirement:** Desidera must obtain and
scientifically validate an **empirical, observed-star-derived effective PSF
for every band being used in subsequent galaxy photometry**, with no
filter-specific exceptions. In the Leo P DR11 south native coadd analysis
this means **g, r, i and z**; a scientifically incomplete `g`
means **all-band galaxy photometry cannot start**. The current r/i/z
PSF-0 reconstructions remain `EXPERIMENTAL_PSF_NOT_ADOPTED` and
also **cannot** satisfy the gate. A diagnostic coverage report, a
numerically converged PSF, or a PSFSIZE seeing estimate do not satisfy
the scientific requirement. The prior pilot's honest `g` refusal
closes **only a diagnostic experiment matrix**, not the empirical PSF
development objective.

Define the required input to a future numerical photometry executor
as one *trusted internal science receipt per exact band/observational
dataset*, recording:
- empirical origin from **observed stellar pixels in that band**;
- relevant coadd/CCD observation lineage and verified original hashes;
- properly normalized final model with explicit immutable kernel hash;
- independent stellar evidence (at least 3 Gaia-identified holdouts in
  the current primary policy), extended-wing validation, spatial
  representativity, stellar/sky separability, correlated coadd noise QA;
- explicit **scientific adoption** distinct from experimental preview.

`inspect_empirical_psf_coverage` and
`require_empirical_psf_coverage` in
`src/lsbpipe/photometry/workflow_dependencies.py` express the
**fail-closed planning/entry contract** for later photometric execution.
They accept only future *trusted* internally verified science receipts;
the contract cannot authenticate arbitrary user-entered dictionaries
or replace the actual measurement QA. There is **no numerical
photometry executor yet** to invoke this guard, so these additions
must not be misrepresented as scientific validation or as a
retroactive upgrade of PSF-0. Subsequent executor implementation
**must invoke the guard before any scientific measurement is written**.

An external single-CCD PSFEx kernel, a Gaussian from PSFSIZE, and
an uncalibrated analytically imposed Moffat profile are **not
acceptable substitutes** for the mandated empirical coadd PSF.
PSFEx and analytical shapes remain useful for benchmark comparisons,
initialization and contamination models; the *final* adopted PSF
must be constrained and independently verified by real observed
stars in the required filter and observational product.

### How to recover the missing Leo P g-band stellar PSF without fake passes

**Priority 1 — improve the sample selection, not the acceptance cut.**
Read the complete `g` Gaia/Tractor first-failure inventory (972
screened). Specifically inspect *Gaia-confirmed* sources rejected by
the 85-pixel isolation condition or source-neighbour tests, rather than
reclassifying rejected Tractor galaxy models as stars. For each
candidate inspect native cutouts, weighted segmentation and companion
flux; assess whether *PSF-model-based neighbour subtraction or
neighbour-only masking* leaves a clean, symmetric stellar core and
sufficient azimuthal coverage for the 30–38-pixel local-sky
annulus. Any new neighbour-aware regime must be independently
versioned, predeclared, and verified using **g-only stellar pixels**
and fixed heldouts; modified acceptance criteria are never
retrospectively compared as if the same recipe.

**Priority 2 — leverage more observed g-band star pixels while
preserving coadd compatibility.** Explore supplementary DR11 nearby
brick data **only** when the contributing exposure mix, seeing,
position and coadd resampling can be shown to support transferring
or spatially interpolating the empirical PSF to Leo P. Do not claim
adjacent-brick PSFs are automatically interchangeable. For severe
star scarcity consider observing-frame/CCD-based **empirical**
per-exposure ePSFs, transformed and combined using actual
coadd weights and astrometry to obtain the effective g-band
PSF at the target. Validate that synthesized coadd-equivalent
kernel with stars from the **actual target coadd**, or report
unvalidated transfer and continue blocking photometry.

**Priority 3 — transparent nonstandard validation if samples remain
sparse.** Leave-one-star-out or K-fold refitting can provide
cross-fitted diagnostics of star-derived g PSF stability, but these
folds **do not magically supply three independent Gaia holdouts
of the final all-stars model** and cannot be silently counted as
the original 10-train+3-heldout gate. Any alternative policy must
define a separate statistically justified validation contract,
include injection/sky/covariance tests and be explicitly reviewed
before becoming eligible for scientific adoption.

If real g observations cannot support a scientifically validated
empirical coadd-effective PSF by any supported path, the pipeline
must continue to **block scientific galaxy photometry** and clearly
record why. The requirement does not imply that a trustworthy
PSF can be mathematically guaranteed in arbitrary poor-quality
data; it implies that missing PSF can never be silently ignored.

Primary sources: Legacy DR11 PSF and CCD-model description at
https://www.legacysurvey.org/dr11/description/ and Photutils
`EPSFBuilder` at
https://docs.astropy.org/projects/photutils/en/stable/user_guide/epsf_building.html.
