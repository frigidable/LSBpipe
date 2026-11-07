# PSF-0 first observational pilot: Leo P

Date: 2026-11-03. Status: **numerical, experimental pipeline implementation**.
This document defines executable input and output contracts; it is not
a report of a successful analysis on Leo P FITS. Synthetic tests and the
Windows/Linux CI may run; user-local native FITS must be verified separately.

## The scientific pilot is Leo P, not NGC2146sat

Leo P is the **first mandatory real-object control**. NGC2146sat is a
secondary methodological comparison because a researcher-built empirical
PSF already exists there; it is not the first pilot and must not replace
Leo P in script defaults, test documentation, or acceptance gates.

Working saved Desidera project center:
- ICRS (155.438012 deg, +18.088025 deg)
- Selected observational set per the user's current GUI: DR11 South,
  brick 1554p180, r-band (4/4 local FITS displayed in the earlier
  project screenshot; the repository does not contain these local FITS).
- Additional g/z are **candidates only**, verified by file existence
  and then independently through the strict original-FITS adapter;
  never create a missing band, download it implicitly or claim coverage.
- Literature positions may differ by tens of arcseconds depending on
  adopted center/definition. The script checks the project center
  against the recorded working center within 45 arcsec; this does NOT
  validate physical membership, morphological center or photometric
  ellipse. Leo P has resolved stars and irregular star formation,
  so target masking must not use the bright knots as an automatic
  empirical PSF training sample.

The Leo P source identity is checked *before* any numerical pilot runs.
A generous initial target protection area (at least 90 arcsec radius,
configurable and requiring researcher review) is used to avoid selecting
Leo P stars as foreground PSF trainers. This preliminary circle is not
a scientifically validated target extent.

## Numerical core now provided

`src/lsbpipe/photometry/psf_empirical.py`:
1. Requires an existing `NativeBandFrame` from the strict quartet
   adapter. Never accepts a remote Viewer RGB image or cached screen
   resolution in place of photometry.
2. Generates conservative target protection around the explicit
   project ICRS center using local WCS (small-field approximation).
3. Computes initial source-detection sky and RMS from valid pixels
   outside protection; this is **not final galaxy sky**.
4. Uses `photutils.DAOStarFinder` for candidate positions with a
   user-controlled approximate FWHM and absolute detection threshold.
   Detection does not by itself certify star/galaxy classification.
5. Applies saturation/ALLMASK overrides, native validity,
   sufficient unmasked cutout fraction, crowding, star core contrast,
   sky-annulus quality and local sky-gradient rejection.
6. Fits local planar sky to each star's annular pixels using robust
   iterations. This is deliberately separate from galaxy residual
   sky, and may still absorb wide PSF wings; that limitation is flagged.
7. Builds native-grid empirical core ePSF using photutils
   `EPSFStar`/`EPSFBuilder` from independently sky-subtracted
   cutouts, explicitly weighted by native diagonal INVVAR.
   The configured native sampling is 1. Oversampling 2/4 is **not**
   silently claimed: adaptive oversampling and its star-count tests
   remain a follow-up requirement.
8. Holds out spatially sorted sources from model construction and
   refits only their amplitude to the empirical kernel; reports
   median core absolute residual relative to peak. This is a
   **diagnostic** rather than calibrated chi-square or independent
   high-precision PSF error budget.
9. Keeps training and held-out stars with sky positions and local
   sky metrics; rejects a result with insufficient sample or severe
   negative fitted-PSF ringing.
10. Emits `EXPERIMENTAL_PSF_NOT_ADOPTED` and warning flags for
    unvalidated wings, local spatial representativity, correlated
    coadd noise, unverified Gaia identity, preliminary sky and
    potential PSF reconstruction nonconvergence.
11. If and only if input quartets carry the strict loader's local
    SHA-256 evidence, creates a dataset/band/instrument/selection/sky/
    recipe-bound `PSFLineageKey`. A digest is **not** a quality
    score or a proof of remote archive provenance.
12. On explicit `--save-diagnostic` only, creates a versioned
    `analyses/psf-pilots/psf0-<hash>/` with an original-normalized
    native kernel FITS, QA JSON and caution. It refuses overwrite
    and never touches original FITS. It does not insert the output
    into the scientific accepted-results registry.

## Primary workflow: existing Windows GUI and galaxy projects

The only supported day-to-day developer and researcher path is the existing
`C:\\Users\\frigi\\Documents\\PhD\\Desidera` checkout and its
existing `.venv`. Do **not** create a separate worktree, venv, or copy
of the project. The existing Leo P project is opened via Galaxy Projects.

1. Run the established `lsbpipe.gui` application from the same
   checkout, with the existing virtual environment.
2. In **Galaxy Projects**, open the already saved **Leo P**.
3. Enter **Galaxy Workspace → Photometry**.
4. In **04 · Empirical PSF**, choose an actually present band
   (e.g., `r`, even if the saved Galaxy Workspace default is `g`),
   review the initial guessed detection width and the explicit
   conservative target protection radius.
5. Click **Build PSF preview**. Existing background task controller
   validates real original FITS/WCS/units and computes a diagnostic
   ePSF without writing an output FITS or modifying the project selection.
   GUI stays responsive, with existing Task Stop semantics; the
   photutils fitting phase itself is not presently interruptible.
6. If successful, inspect training vs independent holdout stellar
   positions, reconstructed native-grid PSF image, residual diagnostic
   and all scientific flags. The result is **experimental**, not
   adopted science.
7. Optionally click **Save diagnostic FITS + report**. An independent
   GUI task rehashes all four original files to detect changes,
   then publishes an immutable experimental derivative within
   the SAME Leo P project directory. Previously saved outputs are
   never overwritten.
8. If the model is scientifically rejected (e.g., strong negative
   ePSF ringing), inspect the rejected star sample, the negative
   mass fraction and background/selection flags directly in the GUI;
   no invalid PSF kernel is accepted or exported. No automatic
   weakening of QA thresholds is performed.

The user's first actual run on 2026-11-03 found Leo P, with the saved
selected `DR11 South / 1554p180 / g` band. The requested **r**
original quartet exists independently. That real-r run returned
`PSF0_FAILED_NOT_ADOPTED` due to
`Strong negative EPSF ringing; inspect star selection/sky`.
It is therefore **not** a validated observational PSF.

**Real g-band GUI evidence (researcher screenshots, 2026-11-03):** all
four FITS paths were found; the initial empirical reconstruction used
74 training stars and 19 independent holdout stars, with 28 isolation,
5 pixel-quality/target and 1 image-edge rejection. The signed
ePSF had 4.09% negative absolute mass against an **experimental,
uncalibrated 2.50% screening threshold** and was correctly rejected.
These observations are not a measured FWHM, a validated PSF or evidence
of unusable stellar data; they motivate radial, per-star and
solver-version diagnostics.

The new GUI examines **unmodified signed ePSF values**, separates
negative mass into core/intermediate/outer zones defined using the
*assumed detection FWHM* (not a measured FWHM), records the effective
photutils version/solver defaults, shows a cyan-positive/red-negative
diagnostic image, and offers **Copy QA report** without writing files
or adopting a science result. Do not lower the 2.5% provisional
cutoff or adopt the clipped kernel simply to pass this diagnostic.

Photutils 3.1 changed default ePSF smoothing and center-fit boxes;
at a native-grid assumed FWHM of roughly 4.8 px its auto smoothing
may apply no smoothing, as documented by photutils. This is a
**hypothesis requiring real g/r star-cutout verification**, not a
demonstrated cause of the observed 4.09% negative mass.
See https://docs.astropy.org/projects/photutils/en/stable/whats_new/3.1.html
and https://docs.astropy.org/projects/photutils/en/stable/user_guide/epsf_building.html .

After the stacked PRs are reviewed and available in the existing
checkout, start the GUI with the existing interpreter:

```powershell
Set-Location "C:\\Users\\frigi\\Documents\\PhD\\Desidera"
$py = ".\\.venv\\Scripts\\python.exe"
& $py -m lsbpipe.gui
```

If `photutils` is not installed in the **existing** `.venv`, install
the project's current optional science dependencies there; do not
create a new `.venv`. The code on the current unmerged PR branch is
not obtained merely by running `git pull origin main`.

## Optional legacy CLI diagnostic (not required for GUI work)

From the existing Desidera repository, with dependencies installed:

```powershell
Set-Location "C:\Users\frigi\Documents\PhD\Desidera"
git status -sb
# After approved PRs are merged, sync main with --ff-only.

$py = ".\.venv\Scripts\python.exe"
& $py scripts/psf0_leo_p.py --project-root "C:\path\to\Leo_P" --bands r
# Read-only by default: produces JSON stdout but writes no derivative FITS.

# After reviewing the pilot:
& $py scripts/psf0_leo_p.py --project-root "C:\path\to\Leo_P" --bands r g z --save-diagnostic
```

Do **not** enter a guessed Leo P folder path. The actual project
directory must be selected from Galaxy Projects / its saved manifest.
The script will not download missing bands and will stop without
promoting synthetic or source-unverified data.

CLI flag `--fwhm-pix` sets an explicit *detection guess*, not a
measured PSF width. The source-image scale in arcsec/pixel must be
read from WCS; never assume 0.262 exactly on arbitrary CCD files.

## Controlled post-failure diagnosis: Leo P g-band (PSF-0)

The actual GUI screenshot provides **4.09% signed negative absolute ePSF
mass entirely at radii >2 × the guessed 4.8 px FWHM**, with **zero**
reported contribution from the two inner zones. The source star cutout
background was estimated in an annulus at 10–15 pixels. Since the
reported outer zone begins at 9.6 pixels, overlap makes outer-wing
sky subtraction a **high-priority hypothesis**, not an established
cause. The solver also reports `converged=False` after 8 iterations
in photutils 3.1.1. Those are separate failure indicators.

The GUI now provides four reproducible diagnostic experiments:

| Experiment | Change | Scientific question |
|---|---|---|
| `baseline` | 31px stamp, sky 10–15px, isolation 32px, 8 iter | Reproduce the original 4.09% g result |
| `outer_sky` | 51px stamp, sky 19–24px, isolation 55px, 8 iter | Are the outer negative wings sensitive to a more distant local sky? |
| `wing_smoothing_off` | 31px, default sky/stars, disable photutils final wing smoothing | Is the photutils 3.1 final wing smoothing contributing to the signed outer residual? |
| `longer_convergence` | 31px, default sky, 16 iter | Does the ePSF approach centroid/shape convergence with more iterations? |

Each run is still **non-adopted** and uses the existing Leo P FITS
without downloads, new folders/environments or permanent project-band
changes. Diagnostic results and cumulative same-band attempt
summaries remain visible in the Photometry Workspace; **Copy QA
report** includes the current detailed result and recent attempt
summary. Neither the comparison history nor a lower negative
fraction certifies the model.

The `outer_sky` mode alters the cutout, sky annulus **and isolation
footprint** together. Different stars may be included; the outcome is
therefore not a clean single-factor causal test. Check stellar
overlap, number of accepted/heldout stars and their ICRS positions
before interpreting its effect on negative PSF mass.

Note: in photutils 3.1, `smoothing_kernel='auto'` may return
`smoothing_kernel=None` for well-behaved native-grid PSF widths,
while **independent final wing smoothing may still be enabled**.
The controls report these two mechanisms separately. Official API:
https://docs.astropy.org/projects/photutils/en/stable/api/photutils.psf.EPSFBuilder.html .

The diagnostic computes additional **independent signed-kernel
holdout residuals** before any negative-value clipping, in the same
core/intermediate/outer radial zones. For each held-out star, fit
only the positive source amplitude in the core while keeping the
unmodified signed candidate ePSF; evaluate signed and absolute
residuals divided by the observed peak outside the fitted core.
Then summarize the medians across held-out stars per zone. This
guards against an apparently cleaner-looking PSF which predicts
real star wings less accurately. These diagnostics are not a
noise-whitened chi-square or a calibrated PSF-leakage budget.

Interpret candidate changes in this order: (a) sufficient and
comparable clean stars, (b) convergence fraction, (c) signed mass
distribution AND independently held-out *outer-zone residuals*,
(d) PSF spatial/wide-angle representativity, (e) estimated
galaxy-profile bias. A variant that merely passes the provisional
2.5% mass threshold **must not** be automatically adopted or
ranked above a lower-bias alternative.

## PSF training-star mosaic, manual exclusion, survey priors (P1.4b)

### Native-grid review in the existing GUI

After the first successful **or QA-rejected** ePSF run, the same Leo P
workspace enables **Review training stars…**. A separate scrollable
review window shows one card per original train candidate:

1. Original background-subtracted 2D native cutout, preserving signed
   pixel values and visually marking invalid pixels.
2. The particular candidate ePSF *evaluated at that star's subpixel
   center and fitted to its inner valid pixels*. Both 2D images use
   the same native pixel footprint and asinh display scale.
3. A separate **square** one-dimensional radial profile (native
   pixel radius vs signed normalized intensity), with observed
   and signed modeled profiles plotted together. The square
   graph and 2D cutouts have the same on-screen height/width,
   but their physical axis units are deliberately different.

Every card shows its stable `star-XXXX` identifier, native (0-based)
coordinates, sky position, local background and local gradient.
The training catalogue and entire mosaic are kept in memory by the
existing Qt worker; **no additional cutout FITS are written**.
The figure is for assessment and never certifies a PSF.

Check **Exclude from PSF training** for visually contaminated
candidates (cosmic rays, blends, background gradients, morphological
non-stellar objects or poor centroid quality). The checkboxes are
initialized from the review state. Only after **Apply, save exclusions
& rebuild** are the IDs added to the immutable numerical recipe,
the same real FITS reloaded and a fresh ePSF computed. This explicit
action stores a content-addressed JSON review receipt under the
existing Leo P project's `analyses/psf-pilots/star-reviews/` directory,
with original-source SHA-256 values. The native source FITS and
Galaxy Workspace selected band are unaffected.

**Scientific contamination control:** holdout selection happens
*before* applying manual training exclusions. A holdout star has
no exclusion checkbox and cannot be shifted into or removed from
the test set to improve diagnostics. Training exclusions change
the recipe hash and lineage, and cannot leave fewer than the
configured minimum training stars. The reviewer may restore all
train stars and explicitly rebuild.

Receipts and the model are **non-adopted**; successful solver
execution and a clean looking training sample do not waive the
independent holdout/wings/spatial representativity criteria.
The saved manual review refers to a specific source quartet and
recipe; unrelated filters must not automatically copy pixel-level
exclusions. Cross-band contamination edits should be expressed
as shared ICRS footprints through the future Scene/Smart Mask
workflow, not as PSF training-star exclusions reused blindly.

### DR11 Tractor / Gaia DR3-assisted stellar source screening

The **Leo P original-brick catalogue** is
`<project.data_root>/dr11/south/tractor/155/tractor-1554p180.fits`.
This corresponds to the exact original DR11 `south` brick 1554p180;
it is not a Sky Viewer glyph overlay, a Gaia TAP crossmatch made at an
arbitrary epoch, or a catalogue from another release.

The GUI exposes two explicit, non-interchangeable policies:

- `dao_diagnostic`: legacy DAOStarFinder candidates, image pixel
  validity and isolation only. Their stellar identity is **NOT
  verified**, as demonstrated by the real Leo P mosaic.
- `gaia_strict`: needs a versioned, locally validated DR11 Tractor FITS
  and its local SHA256. If absent or invalid, the operation fails
  closed. The researcher can click **Get DR11 Tractor + Gaia…**
  in the *existing* Leo P Photometry workspace to request the exact
  original FITS from the official Legacy Surveys archive. It is
  added to the same project's original-data tree without modifying
  the native optical quartets or creating a separate worktree/project.
  It is never automatically downloaded during a PSF run.

Before fitting, crossmatch each positive DAO detection against the
project's native Tractor catalogue by the `RA`, `DEC` Tractor
position projected into the **original IMAGE WCS**, with a bounded
2.5 native-pixel match radius and explicit rejection of ambiguous
multiple matches. Require:

1. `REF_CAT='G3'`, positive `REF_ID` (actual Gaia DR3 source ID).
2. `TYPE='PSF'` **and** `FITBITS` without
   `FORCED_POINTSOURCE` or `FROZEN`. In mask regions,
   Legacy Surveys can otherwise force galaxy/foreground sources
   to `TYPE='PSF'`.
3. `BRICK_PRIMARY`, no Gaia duplicate and, when provided, positive
   relevant native-band `FLUX_IVAR`; reject disallowed BRIGHT,
   BAILOUT, GALAXY, CLUSTER, RESOLVED regions and relevant saturation
   and ALLMASK bits. The source is thus an actual fitted source, not
   a Gaia-only retained photometric conversion.
4. The same-band `FLUX_<band>` and `FLUX_IVAR_<band>` must
   exist, be finite and positive; require the heuristic
   `flux * sqrt(flux_ivar) >= 20` (PSFEx-style minimum S/N).
   This *is not* calibrated S/N because Legacy coadds have
   correlated pixel noise and imperfect model flux uncertainties.
5. Match the *complete same-brick Tractor catalogue*, including
   objects too faint for DAO detection, around each star. Reject
   a nearby source with positive fitted coadd S/N >= 5 and
   >2% of the candidate's flux within a cutout-scaled neighborhood
   (the active builder sets approximately 0.65 times the cutout
   size, never exceeding 50 pixels). This is a provisional
   conservative contamination heuristic; blending and projected
   neighbours need direct cutout inspection.
6. Independent *native image* local background/isolation checks, and
   conservative uncalibrated second-moment shape checks (elongation,
   centroid discrepancy, anomalous field-relative core width).
   A Gaia match is **not** a certified clean stellar cutout.

The definitive sample is split into deterministic training and
independent holdout **before** subsequent manual training exclusions.
Both train and holdout must undergo the same initial Gaia and native
cutout screening; neither controls later manual selection of holdouts.
The report and mosaic expose Gaia source ID, G magnitude when available,
crossmatch separation, morphology metrics, catalogue SHA256 and
detailed rejection counts. Catalogue identity becomes part of
the PSF lineage digest; old selection receipts cannot silently carry
across changes in the catalogue, band, protection or recipe.

The 2.5-pixel radius, ellipticity and relative width cuts are
**provisional screening heuristics**, not statistically calibrated
selection functions or measurements of the PSF width. Their effect on
stellar completeness, spatial sampling, color/magnitude dependence
and independent holdout residuals must be tested on real Leo P
data. Gaia DR3 misses some faint sources that may be useful ePSF
stars; neither the Gaia-verified nor DAO baseline mode should be
declared universally superior without quality and selection-bias QA.

Reference:
https://www.legacysurvey.org/dr11/catalogs/
https://www.legacysurvey.org/dr11/bitmasks/
https://www.legacysurvey.org/dr11/description/
https://www.legacysurvey.org/dr11/files/

### Real Leo P diagnostic: bright-star rejection bias (November 2026)

The *first* two-pass experiment on DR11 south 1554p180, g, using
the same 51-pixel cutouts and Gaia-strict selection as untrimmed
Extended, retained 34/40 training stars after dropping 4+2;
10 held-out stars remained fixed. The observed comparison:

| Metric | Extended | Old unconditional two-pass trim |
|---|---:|---:|
| train / holdout | 40 / 10 | 34 / 10 |
| negative signed ePSF mass | 0.73% | 1.39% |
| outer held-out median |residual| / peak | 0.00735 | 0.00742 |
| core held-out median |residual| / peak | ≈0.0200 | 0.0198 |
| reported ePSF non-convergence | not established from same QA text | flagged |

The raw QA's per-star trial score was found to be **strongly monotonic
in diagnostic model amplitude**, Spearman rho = 0.891 (40 stars)
in pass 1 and 0.873 (36) in pass 2. First-pass excluded objects
had median diagnostic amplitude ≈370 (internal model normalization),
vs ≈10.2 for retained objects. The reported model-amplitude field
must NOT be interpreted as an independently calibrated total-flux
measurement. This is descriptive association, not proof of a specific
physical cause of mismatch.

**Safety response:** retain the provisional experiment for regression,
but before *applying* its candidate exclusions, audit the rank
association between PSF fit amplitude and score. For ≥20 stars and
positive rank correlation >=0.75 with at least one proposed exclusion,
**fail closed**: keep all original training stars, record the proposed
IDs/rho/median amplitude and explicit `AUTO_TRAINING_TRIM_BLOCKED_BY_SCORE_FLUX_COUPLING`
flag; show it prominently in the GUI. The threshold 0.75 is
a deliberately conservative *uncalibrated diagnostic guard*, not a
statistically derived probability or a photometric quality standard.

This prevents a biased clipping procedure from selecting *only*
apparently well-fitting faint stars and losing the brighter objects
that constrain wings. It does NOT validate the untrimmed ePSF.
Critical follow-ups: train-only cross-fitted / leave-one-out residual
profiles, compare in *flux-matched* strata; inspect excluded-star
cutouts for blending, background annuli and saturation; model spatial
PSF variation and color; rerun synthetic injected blends and
wide-wing backgrounds. Avoid adaptively tuning cuts against the same
10 holdouts: they are an evaluation set, not training data.

### Iterative training-star outlier screening and sky audit (P1.4c)

**Motivation (real Leo P):** Gaia-strict baseline selected 74 training
stars / 19 independent holdouts; 3.06% absolute negative signed
ePSF mass exceeded the provisional 2.50% rejection limit.
The extended 51-pixel cutout produced a provisional model with
0.0194 median holdout core |residual| / star peak. These numbers
cannot isolate cause: the original 31→51 experiment changes
cutout size, isolation radius and sky annulus simultaneously.
A Gaia DR3 ID and Tractor `TYPE=PSF` still do not guarantee an
isolated, uncontaminated ePSF cutout.

**Reproducible new optional experiment** `outer_sky_robust`,
explicitly labeled in the same Leo P PSF GUI. It keeps the exact
51-pixel, radius 19–24 annulus and 55-pixel source-isolation recipe
of `outer_sky`, but adds up to **two** iterative star-level outlier
screening passes:

1. Fix the deterministic independent holdout and user-approved
   manual train-only exclusions **before any modelling**.
2. Build a preliminary signed ePSF on training stars only using
   `EPSFBuilder`'s existing pixelwise sigma-clipped stack.
3. Independently score *each training star* against the trial ePSF
   shifted to its native subpixel centroid. Fit the star amplitude
   on valid core pixels; assess 75th-percentile absolute core
   residual, 90th-percentile outer residual and a 99th-percentile
   global residual statistic. Normalize with the coadd's diagonal
   IVAR, local background RMS and a conservative 3% model-error
   floor; **these are heuristic scores, not covariance-calibrated
   chi-square**.
4. Flag exceptionally poor model matches relative to the training
   distribution's median and robust MAD; require >1.8x median
   simultaneously. Reject at most 12% of active train candidates
   per pass and 20% of original train total, never dropping below
   `min_train_stars`. Create a fresh EPSFBuilder for each refit;
   stop early if no star meets the outlier threshold.
5. Report every candidate score, screening cutoff, selected
   automatic exclusions, iteration number and fixed-holdout
   evidence. Visualize auto-rejected candidates separately in
   the read-only mosaic; they cannot be manually restored
   within this experiment. To restore them, switch to `outer_sky`
   or another untrimmed experiment.
6. Validate the *final* signed ePSF, convergence and independent
   holdout residuals using existing gates; never tune the
   threshold against holdout metrics. A model that remains
   QA-rejected remains a rejected model.

This resembles PSFEx's whole-object iterative residual clipping
only in design; it is **not a reproduction** of PSFEx's calibrated
detector noise model or spatial PSF fit.
Official methods: https://psfex.readthedocs.io/en/latest/Working.html ,
https://psfex.readthedocs.io/en/latest/GettingStarted.html ,
https://docs.astropy.org/projects/photutils/en/stable/user_guide/epsf_building.html .

**Sky treatment audit.** Current Desidera does NOT fit a physical
point-source wing model jointly with local background. For each
source it clips outliers and robustly fits a **plane in an annulus**
(10–15 px baseline, 19–24 px extended). The fit is now recomputed
*after* the final clip; previously the returned coefficients
corresponded to the preceding selection of pixels. The local
sky value and gradient are subtracted from the cutout prior to
`EPSFBuilder`, consistent with Photutils' explicit contract, but
the measured annulus includes possible stellar-wing light and
neighbor contributions. Even sigma clipping cannot remove a
spatially smooth stellar halo.

The model reports the inner/outer annulus radii in units of
**assumed detection FWHM** (not measured PSF), retained sky fraction,
within-annulus radial median change and octant-to-octant
background heterogeneity. These statistics flag sky contamination
and wing degeneracy but **cannot correct the lost stellar flux**.
For FWHM guess 4.8 px, the 10–15 px ring starts at 2.08× guessed
FWHM; the 19–24 px ring starts at 3.96× guessed FWHM. Both require
real wing validation before use in calibrated photometry.

**Next required scientific step**: a model (D_i=A,P_i+B_0+
B_x x_i+B_y y_i + N_i + epsilon_i) simultaneously fitting a
nonnegative physically plausible PSF core/wing model, local
background, and neighbor models or masks on enlarged original
FITS regions. Mask all nearby sources before local-sky
regression; compare annuli at multiple large radii and report
radial background stability. Use multiscale holdout sky
perturbations and injection/recovery of simulated winged PSFs,
close pairs, unresolved galaxies and varying background gradients.
Use exposure-dependent PSFEx on original CCDs as an external
reference when available. Existing coadd IVAR is only diagonal,
so no covariance-calibrated chi-square or confidence intervals
may be claimed at this stage.

**Disqualifiers before adoption:** persistent signed negative
ePSF wings, photutils non-convergence, sample spatial or
magnitude/color dependence, contaminated sky annuli, inadequate
independent holdout statistics, or evidence that clipping
only improves in-sample profiles while degrading spatial
coverage. Avoid relaxing the negative-mass/convergence gates
just to report a successful PSF.

References:
https://psfex.readthedocs.io/en/latest/Working.html
https://iraf.readthedocs.io/en/doc-autoupdate/tasks/noao/digiphot/daophot/allstar.html
https://docs.astropy.org/projects/photutils/en/stable/user_guide/epsf_building.html
https://www.legacysurvey.org/dr11/description/

### Initial FWHM versus Leo P protected footprint

**Current default** `FWHM guess = 4.80 native pixels` originates
from the hard-coded PSF0Config and is passed to `DAOStarFinder`.
It is a detection prior, not a fitted seeing value.

**New optional GUI control** `From DESI PSFSIZE…`: if the project
already contains original `legacysurvey-<brick>-psfsize-<band>.fits.fz`,
load the local per-pixel **weighted mean PSF FWHM in arcsec** from a
5x5 area around the object's exact ICRS position. Verify array
geometry and WCS compatibility with the selected original IMAGE;
convert arcseconds into native pixels using the true image WCS.
The resulting proposed FWHM is per-band, recorded with local SHA-256
and survey source type, and displayed explicitly as a **prior**.
If no map is available, do NOT download or infer a made-up seeing;
keep the manual guess with a descriptive notice. The user's native
quartet has four files, so the separate optional PSFSIZE map may
not exist locally. A brick catalogue column (`psfsize_g`, etc.)
is an alternative *future* source, which must not be conflated
with the spatially varying per-pixel map.

**Current default** `Protect target radius = max(90″,
project.target_radius_arcsec)`. The saved project data structure's
initial target radius is itself a 60″ configuration default.
Neither quantity is a measured Leo P isophotal radius.

Future scientifically grounded **automatic galaxy protection**
should come from low-threshold, PSF-aware, multi-band segmentation
and provisional outer isophotes, with an uncertainty margin and
manual review. The PSFSIZE map does **not** determine the galaxy's
angular extent. Do not tie the protection radius mechanically to
seeing, nor declare an automatically fitted isophote as a final
galaxy size before residual-sky and contamination QA.

### References

- Official DR11 files describing original per-pixel PSFSIZE maps,
  weighted PSF FWHM in arcsec and brick-level fields:
  https://www.legacysurvey.org/dr11/files/
- Photutils 3.1.1 EPSFBuilder, star-fitting and cutout conventions:
  https://photutils.readthedocs.io/en/3.1.1/api/photutils.psf.EPSFBuilder.html

## Blocking issues before publishing Leo P science

These items are required, even after successful synthetic unit tests:

- Run on the actual Leo P **original** DR11 South r-band quartet,
  record source hashes, image size and which FITS paths exist.
- Inspect all detected stellar candidates (training, holdout and
  rejected) on native FITS. Gaia evidence may be used for
  cross-identification; this is not yet automated.
- Assess which field is large enough: a small galaxy cutout may
  contain too few isolated stars for an empirical PSF. In that case,
  do not silently shrink the training/holdout thresholds—use a
  verified whole-brick reference or explicitly documented survey PSF.
- Check galaxy protection conservatively against real Leo P diffuse
  light and stellar population; don't mask real galaxy members as
  external PSF stars.
- Validate ePSF centroid, subpixel phase coverage, field variation,
  FWHM/ellipticity/radial encircled energy, 2D residuals and heldout
  stars. Current core result alone does not do those tests.
- Build extended wings with more suitable bright stars, distinguish
  the **remaining** DECam halo from survey-subtracted light.
  Do not model/subtract the full halo a second time.
- Test PSF choice on injected extended/diffuse Leo P-like galaxies,
  colors and cross-band PSF matching; propagate systematics to the
  scientific result. Only after those gates may a PSF be adopted.
- Validate 2/4x ePSF oversampling with star count and subpixel phases
  if any band proves insufficiently sampled. Do not force it by default.
- Any SBF analysis requires its own spatial-frequency/noise-mask QA.

## Scientific references

- Photutils ePSF building, which requires background-subtracted,
  carefully selected, isolated stars:
  https://docs.astropy.org/projects/photutils/en/stable/user_guide/epsf_building.html
- EPSFBuilder API and convergence diagnostics:
  https://docs.astropy.org/projects/photutils/en/stable/api/photutils.psf.EPSFBuilder.html
- Legacy DR11 per-CCD PSFEx and DECam extended halo treatment:
  https://www.legacysurvey.org/dr11/description/
- Leo P early physical/structural literature:
  https://doi.org/10.1088/0004-6256/146/6/145

**No observational Leo P PSF measurement has been produced as part
of creating this PR.** Its success is evaluated only after executing
the script against the user's real, locally stored original FITS.
