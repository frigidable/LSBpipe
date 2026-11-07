# PSF-0: FWHM-scaled local-star sky geometry (experimental)

**Scope:** Leo P-first DR11 south / g,r,i,z native coadd PSF-0. The original FITS are read-only. This change is a diagnostic **recipe change**, not a statement that PSF wings/sky are solved, and not a scientifically adopted PSF. PSFSIZE values are survey-weighted seeing estimates, **not** empirical ePSF measurements.

## Diagnosed problem

For Leo P DR11 `g`, the original native coadd `psfsize-g` map gives `1.721 arcsec` at `0.2620 arcsec/pix`, a detection FWHM prior of `6.57 pix`. The former fixed Extended sky annulus `19–24 pix` samples only **2.89–3.65 × FWHM**, where stellar wings may bias the fitted local sky plane. Sigma clipping does not eliminate smooth wing light. This invalidates any automatic claim that a low stellar residual confirms correct background removal.

Official photutils guidance: https://docs.astropy.org/projects/photutils/en/stable/user_guide/epsf_building.html . DR11 seeing/FWHM: https://www.legacysurvey.org/dr11/files/ .

## Active, exactly six, deliberately non-adopted recipes

| Mode | Cutout / annulus / isolation | Interpretation |
|---|---|---|
| `baseline` | fixed 31 pix / 10–15 px / 32 px | Retained *as an explicitly wing-sensitive reference only*, NOT correct sky |
| `outer_sky` | adaptive `r_inner=ceil(4.5 FWHM)`, `r_outer=ceil(5.75 FWHM)`, `size=max(51,2*(r_outer+2)+1)` odd, `isolation=size+4` | Conservative expanded local sky sampling, **not proof of sky free from wings** |
| `outer_sky_robust` | same geometry, plus ≤2 passes of *guarded* train-only residual screening | Screen is still disabled automatically when it tracks star amplitude, and holdouts are not used to choose rejections |
| `outer_sky_convergence` | **exactly the same** adaptive sky annulus, stamp, isolation and untrimmed star sample as `outer_sky`, but `max_iterations=16` instead of 8 | Controlled Photutils convergence A/B (especially for Leo P `i`), never automatic PSF promotion |
| `outer_sky_no_wing_smoothing` | Same sky, Gaia/Tractor policy, 16-iteration cap, star split and no training-star trimming as `outer_sky_convergence`; changes only `wing_smoothing=False` | Controlled final-wing-smoothing A/B, not a science PSF |
| `outer_sky_isolation_footprint` | Same 16-iteration configuration and sky/PSF stamp as `outer_sky_convergence`, but DAO isolation is `cutout_size` rather than `cutout_size+4` native pixels | Targeted Leo P `g` experiment: tests whether four Gaia stars with neighbour separations 81–85 px can be recovered; **not** a proven clean-star selector or science PSF |

At `FWHM=6.57 pix`, both extended modes have `81 × 81` cutouts, `30–38 pix` sky annulus, `85 px` minimum neighbour isolation; ratios **4.57–5.78 × FWHM**. At `FWHM=4.8 pix`, extended geometry is `61 × 61` and `22–28 pix`. Both examples are based on a *guessed seeing prior*, not verified physical ePSF.

If an adequate annulus would require a cutout larger than the PSF-0 safety limit of `91 × 91`, **raise an actionable error** rather than silently moving the sky closer to the star. A baseline reference remains possible but cannot be presented as a validated replacement. The enlarged cutout and isolation constraints may reject many stars; the number of accepted train/holdout sources must be evaluated on real Leo P FITS, not simulated as a pass.

## Scientific restrictions and next validation

1. Per-star 2D signed residual, core/outer profiles, radial trend of sky annulus and octant spread remain **descriptive**, not covariance-calibrated chi-square.
2. `EPSFBuilder` requires background-subtracted input. The Desidera robust planar sky fit is upstream of the ePSF, but it is not a joint star-wing + residual sky model.
3. A `>=4.5 FWHM` annulus may still contain the wings of bright sources, especially if nearby sources or wide-angle scatter are present.
4. Changing seeing or cutout size changes the detection/selection footprint. Cross-recipe comparisons require matched Gaia source IDs, consistent input hashes, and a scientifically justified common population.
5. The batch dispatcher uses distinct `PSFSIZE_{G,R,I,Z}` priors, except explicit per-band manual overrides. A manual `star-XXXX` exclusion from another FWHM is **rejected**, not silently transferred.
6. The detailed `griz` debug JSON includes exact effective recipe + SHA256 and original source hashes for rejected and completed outputs. Strict project UUID, release/brick, and optical-band checks prevent cross-band displays.
7. Future science QA: injection–recovery under a simulated smooth wing, blend, bright/saturated star and sky-gradient grid; multi-annulus background sensitivity; stratified heldout star comparisons (magnitude, colour, position), photutils subpixel-phase diagnostics, and covariance-aware goodness of fit. None are completed here.

**Scientific status:** `EXPERIMENTAL_NOT_ADOPTED` / `DIAGNOSTIC_NOT_ADOPTED`. Input source files have not been modified.


## Leo P griz evidence (2026-11-04) and two scientifically distinct catalog policies

The real DR11 south brick `1554p180` four-band QA was inspected on the local
native FITS, with independently verified Tractor source SHA-256 and per-band
PSFSIZE priors. Under the FWHM-scaled `outer_sky` recipe:

| Band | Prior FWHM (native px) | Strict Gaia/Tractor train+holdout | Signed negative mass | ePSFBuilder converged |
|---|---:|---:|---:|---|
| g | 6.57 | selection underflow: 10 accepted, minimum 13 | not fitted | not fitted |
| r | 4.88 | 16+4 | 1.235% | yes |
| i | 5.12 | 10+3 | 2.133% | **no** (8 iterations) |
| z | 3.99 | 39+10 | 1.430% | yes |

This is observational diagnostic evidence, not a calibrated model adoption.
The g run examined 969 DAO candidates, of which 692 were first rejected by
`NOT_GAIA_DR3`; this category includes **unverified sources of all types**,
not automatically usable stars. Other leading g reasons were 109 strict
Tractor neighbours and 109 missing centroid crossmatches. The i model is
not converged even though its final fitted-star centroid convergence
fraction is 100%: the Photutils build stopped at the configured maximum,
so the complete convergence status must be used. The negative-mass figures
are measured on **signed pre-clipping kernels**, not photometric flux losses.

Two noninterchangeable candidate-training policies are now provided:

- `gaia_strict` (unchanged): original native DR11 Tractor `TYPE=PSF`
  and `REF_CAT=G3`, valid Gaia ID, masks, fitted flux, isolation, shape
  and local sky. Both training and holdout stars have Gaia-confirmed IDs.
- `tractor_psf_validated` (new **EXPERIMENTAL TRAINING POLICY**): same
  original DR11 Tractor brick and the same conservative source/cutout
  gates, with the relative core-width reference measured exclusively
  from Gaia DR3-confirmed stars, but `TYPE=PSF` stars lacking Gaia IDs
  are eligible **only for training**. The expanded mode rejects the
  sample if fewer than five Gaia reference cores establish that prior. Gaia-confirmed stars are independently reserved for the
  holdout **before** building or iteratively screening the training PSF.
  It fails closed if there are fewer than the required Gaia holdouts,
  even if many non-Gaia Tractor stars are available.

Neither `TYPE=PSF` nor a fitted-flux S/N from a coadd is an independent
spectroscopic or high-resolution proof of stellar morphology. Gaia
holdouts are independent of the *ePSF fitting and train-only trimming*, but
the initial candidate preselection (e.g. masks, core moment width) still
uses their cutout pixels. Consequently, Gaia-only holdout residuals are
not an unbiased validation of the preselection classifier. Different
training policies may yield different PSF star samples. Compare
source-ID overlap and report brightness, position, and noise dependence
before attributing residual changes to any individual gate.

A per-candidate `candidate_screening_audit` in the full griz QA JSON now
records each inspected DAO detection's position, peak, nearest DAO
neighbour, catalog-match evidence (including ref-cat, optional Gaia ID,
Tractor type, fitted flux/S/N, mask/fit bits and nearest catalog neighbour),
first refusal reason, passed gates, local-sky QA and shape QA where they
were actually reached, and its pre-model train/holdout role. **Missing
metrics mean that the earlier failure prevented that test**; they are
not imputed. The audit reports `n_screened_candidates` and
`n_unexamined_candidates`, so DAO-only diagnostic caps cannot be
mistaken for complete census. No full native pixel arrays are exported.

The training-only policy is a research control, not a recommendation to
replace Gaia-strict as the default, and it cannot be science-adopted.
The native FITS/Tractor sources remain read-only. Further acceptance
requires explicit per-band injection/recovery, sky-wing and spatial
PSF validation and covariance-aware heldout residual assessment.


## Independent real-image follow-up: Tractor-vetted pilot (2026-11-04 15:09 UTC filename)

The uncommitted local researcher export
`Desidera_LeoP_PSF0_griz_debug_20261104_150909.json` uses the exact
DR11 south `1554p180` Tractor SHA-256
`8e8e3f75af868f3ef7cf4c4c9d7ca28210cbca87ef27c98351a6ab237010562c`.
These figures are **observed diagnostics only**, not clean PSF measurements:

| Band | Total DAO examined | Train (Gaia/Tractor-only) | Gaia-only holdout | Signed negative absolute mass | Solver/QA outcome |
|---|---:|---:|---:|---:|---|
| g | 972 | 11 candidates before split (10 Gaia, 1 Tractor-only) | no model | n/a | Insufficient total 11 vs 13 required |
| r | 882 | 16/10 | 4 | 1.707% | Converged, still NOT_ADOPTED |
| i | 1427 | 10/12 | 3 | 4.825% | Not converged after 8 iterations, blocked |
| z | 675 | 36/39 | 10 | 2.719% | Converged but 2.5% experimental screen failed |

In r/i/z the **training** median native positive-moment core sigmas for
Gaia and Tractor-only are respectively
2.30/3.15, 2.34/3.18, and 2.19/2.68 pixels. The corresponding
median native **peak** fluxes in nMgy/pix are
2.879/0.094, 2.654/0.106, and 2.970/0.214. Comparing unmatched
medians is **not a causal test for galaxy contamination**:
positive-clipped moments are S/N dependent. For example, in the
z 0.2–0.5 nMgy/pix peak bin, already observed median widths
were 2.46 pixels for six Gaia-associated objects and 2.58 for
19 Tractor-only objects. The other bands have poor faint-end Gaia
coverage and hence insufficient brightness-matched controls.

The negative power remains concentrated in the model's **outer**
radii: i contributes about 4.70% of total unsigned mass beyond
2 assumed FWHM and z about 2.72% beyond 2 assumed FWHM. Negative
absolute mass is an uncalibrated shape heuristic, NOT an error rate
or negative physical light. Photutils `converged` is the actual
builder-level Boolean, not just the fraction of recentered stars;
i had a final converged fraction of 1.0 but `converged=False`
because an additional center-shift criterion was unmet.

**Instrumentation repair in this PR:** preserve exact candidate and
training-cap roles in the full QA JSON (including underflow and
cap-unused rows), record median peak flux, native peak/RMS and
positive-core moment width separately for Gaia and Tractor-only,
and provide fixed, **descriptive** peak-flux strata with sample-size
gates on the interpretation. No new automated classifier, PSF
acceptance criterion or loosened selection threshold is introduced.

**Next physical validation:** match Gaia holdout `REF_ID` and source
hashes between strict and pilot runs, perform matched-brightness
train-set comparisons where actual overlap exists, test cutout
background/ring sensitivity, and then investigate PSF spatial
variation and broad wings on real data. Do not use more Tractor-only
stars as evidence of better ePSF by itself.


## Approved PSF-page visualization contract (2026-11-04)

The approved GUI prototype at the project level requires a **persistent**
per-selected-band scientific view: a native PSF model image, a radial
profile/residual diagram and a quality/status summary. These three science
surfaces must never be hidden behind the detailed-QA disclosure. The left
Galaxy Workspace navigation and independent g/r/i/z states remain intact;
per-band configuration and griz batch/export follow the science results.

Data binding is deliberately stricter than a cosmetic illustration:

- **Successful experimental fit:** the 2D model is the *actual in-memory*
  normalized, nonnegative \`PSF0Result.kernel\`. It remains labelled as
  provisional/non-adopted; its signed pre-clipping diagnostic is preserved
  separately in QA. The radial diagram is generated from the real,
  in-memory, local-sky-subtracted native cutouts and the *signed aligned*
  model from the existing star-review worker snapshot. Each training star
  uses its measured native peak as a display normalization and identical
  1-pixel annular masks in observed and model arrays. Each displayed radius
  pools valid stars using the median, for observed intensity, matched-model
  intensity and **observed−model signed residual**.
- **Signed-kernel quality rejection:** an actual computed but
  scientifically rejected signed model may be shown in diagnostic colors
  with explicit REJECTED/non-adopted status. Radial plots are permitted
  only if the worker also supplied the actual paired cutouts/model.
- **Selection underflow / no computed fit / missing inputs:** model and
  radial cards show explicit unavailable explanations. The GUI does **not**
  invent a PSF model, normalize an unavailable model, fabricate residual
  curves or reuse a prior-band model. The complete selection failure
  remains accessible in per-band full QA.
- The radial diagram is **TRAINING-IN-SAMPLE**, not an independent Gaia
  holdout, calibrated chi-square, full PSF-wing validation, or spatial
  PSF constraint. Only stars still marked \`train\` are aggregated;
  \`excluded\` and \`auto_rejected\` cutouts cannot silently contribute.
  The original independent heldout metrics and immutable source
  provenance remain accessible through the same QA surface.

Implementation reuses \`psf_star_review.py\` rather than introducing an
additional image pipeline or directories. All images are rendered in the Qt
GUI from previously computed arrays, without network access, FITS writes,
or changes to any scientific fitting/selection thresholds.

### 2026-11-04: real dark-theme screenshot readability repair

A local Leo P quality-rejected (`G`, training 75, negative-mass 3.23%)
screenshot exposed a **GUI-only** regression: the 540×315 radial chart
was placed into a narrower/shorter QLabel with native-size pixmap
semantics. Qt clipped the header, parts of the axes and residual labels.
The dedicated `_AspectFitProfileLabel` preserves the original plot as an
immutable presentation source and scales it on every resize with
`Qt.KeepAspectRatio`; the fixed-height viewport always displays the
entire scientific diagram. The chart itself now labels the signed
residual as `observed − aligned model / measured stellar peak`, reports
its actual plotting scale and the native-pixel radius ticks, and clips
curves to their respective upper/log and lower/signed plot rectangles.
A Qt regression test covers multiple widths and stale-plot invalidation.
No photometric, training/holdout or science-adoption logic changes.


## PSF-0 methodological hardening: signed holdouts, solver convergence and lineage

The controlled solver experiment \`outer_sky_convergence\` changes **only**
the Photutils EPSFBuilder iteration limit from 8 to 16. This avoids
confounding iteration budget with seeing/FWHM, candidate selection,
local-sky annulus, star masks, and optional star-level trimming. Both
models remain diagnostics, including if the 16-iteration fit converges.
Photutils documentation cautions that a non-converged ePSF can have
percent-level shape/centroid error:
https://docs.astropy.org/projects/photutils/en/stable/user_guide/epsf_building.html

**Nonconvergence is now a scientifically blocking PSF-0 outcome:** when
Photutils returns \`converged=False\`, Desidera preserves the signed kernel,
the real star-review cutouts, the full iteration history and fixed holdout
residuals in a structured \`PSF0QualityError\`. It never labels the model a
successful provisional PSF merely because the
\`final_converged_fraction\` was 100%. More iterations do **not**
guarantee convergence or improved scientific quality. With older
Photutils versions returning only a two-tuple (no convergence Boolean),
the preview remains explicitly `EPSF_CONVERGENCE_NOT_REPORTED_BY_LIBRARY`
and must not be interpreted as convergence-verified. Real Leo P runs
should use a pinned Photutils version exposing this result field.

**Holdout completeness is now a blocking integrity requirement:** all
originally reserved holdout candidates must have a valid *signed*
core-amplitude fit and valid median signed/absolute residuals in **each**
of the three radial ranges (at least four usable native pixels per zone).
Failures and missing annuli cannot be silently omitted from the
denominator. An incomplete test results in
\`INCOMPLETE_FIXED_HOLDOUT_RESIDUAL_QA\`, with the original holdout IDs
intact and the signed diagnostic model available only for review.
This integrity gate does not make the residual metrics covariance
calibrated, independently validate Gaia preselection, or prove PSF
spatial representativity.

In `gaia_strict` and `tractor_psf_validated` the fixed holdouts have
Gaia DR3 identifications (not absolute proof that their cutouts are clean).
In `dao_diagnostic`, the holdouts are **unverified DAO detections**;
their independence is limited to the ePSF training pixels. They must not
be counted as Gaia-vetted or Tractor-only sources. The QA now distinguishes
`n_train_unverified_dao`, `n_holdout_unverified_dao`, and
`holdout_identity_validation`, and uses
`DAO_UNVERIFIED_HOLDOUT` for their final selection roles.

A separate existing diagnostic of the nonnegative clipped-kernel core
now uses the actual **subpixel star centroid** as the centre of its
residual-evaluation aperture, consistent with shifting the model to
that same location. It reports the aperture center, radius and number
of native pixels. Previous code mistakenly centered that aperture on
the unshifted model grid.

**Reproducibility:** the method contract is versioned
\`desidera.psf0-coadd-epsf/2\`. The observation-bound PSF lineage key
now includes a digest of the configuration, effective Photutils solver
defaults, recentering function, relevant package versions and the new
holdout measurement policies. A version change cannot silently reuse
the same lineage key, although a fully reproducible scientific release
still requires a pinned software environment and archived original
FITS. The original \`recipe_digest\` remains separately available.
This change intentionally makes new lineage keys incompatible with
the old diagnostic keys; original FITS and prior experiment JSON remain
unchanged.

## Criteria to close the PSF-0 pilot branch, not to adopt a science PSF

1. Linux and Windows Python test matrices, Qt snapshots, holdout
   integrity and lineage regressions pass on the same HEAD.
2. Re-run real **Leo P g/r/i/z** with Gaia-strict and experimental
   Tractor-vetted selection as appropriate; separately test
   \`outer_sky\` (8 iterations) versus \`outer_sky_convergence\`
   (16 iterations) on the same native FITS, sample IDs, sky and masks.
   Do not assert a result from synthetic fixtures as real-data success.
3. Export and inspect complete original-FITS source SHA-256,
   candidate-selection QA, immutable heldout IDs, convergence history,
   signed negative mass **by radius**, holdout completeness,
   brightness-dependent star morphologies and radial residuals.
4. Do not bypass an underflowing g sample (11 vetted, minimum 13)
   through invented stars; do not accept i solely for convergence; do
   not lower the arbitrary 2.5% signed-negative-mass diagnostic
   screen to force r/z to pass.
5. Merge/close the draft only after the above reproducibility and
   review evidence is accepted. **Even a fully passing PSF-0 milestone
   is a non-adopted, global, core-only diagnostic.** The eventual
   science PSF requires spatial representativity, external PSFEx
   comparison, extended-wing/sky modelling, injection recovery and
   correlated-coadd-noise validation. Those are distinct subsequent
   PSF-science milestones, not validations supplied by PSF-0.


## Additional real Leo P evidence, 2026-11-05 16:35 UTC

Two uploaded griz JSON snapshots have matching `i`-band hashes for the
original DR11 `image`, `invvar`, `maskbits`, and `nexp` inputs.
They do **not** constitute an eight-versus-sixteen iteration experiment:

- At `16:35:00Z`, the selected `i` recipe was
  `outer_sky_robust` (8 iterations, `tractor_psf_validated`,
  `training_outlier_passes=2`), but `science_report_or_rejection`
  was null. Its QUALITY REJECTED status alone provides no usable model
  or convergence metrics.
- At `16:35:18Z`, the completed `i` run used
  `outer_sky_convergence` (16 iterations, **`dao_diagnostic`**,
  `training_outlier_passes=0`). It reported **56 train + 15
  computational holdout DAO candidates**, with **zero Gaia matches and
  no Tractor catalogue used in source screening**. Of 1427 detected
  DAO candidates, only 375 were screened (298 rejected for proximity,
  5 stamp edges, 1 masked stellar core; 71 accepted). The other
  1052 detections were not screened under the diagnostic cap.
- The 16-iteration signed kernel has negative absolute mass
  **3.7075%** (2.50% diagnostic threshold), **entirely beyond
  2 guessed FWHMs**. Builder `converged=False`; final stable-center
  fraction **14.29%** versus required 95%. The iterative fractions
  fluctuated, reaching 92.86% in iteration 10 but declining to 14.29%
  by iteration 16; the last model change was 0.87% of peak. This is
  **not** evidence that adding iterations improves convergence.
- The fixed 15/15 DAO holdout candidates had successfully reported
  residuals in all radial zones, but their stellar identities were
  not independently validated and coadd noise correlations are
  unmodeled. Median absolute signed residuals divided by measured
  peak were **0.0388 / 0.0175 / 0.0120** in core/intermediate/outer
  zones, respectively. These are not calibrated confidence intervals
  or scientific model-acceptance metrics.
- The **g, r and z bands were not computed in these two exports**:
  their original FITS paths were present, but their per-band
  source-specific scientific reports were null. The fact that the
  top-level selector is `dr11/south/1554p180/g` does not make an
  `i`-band result into a `g`-band measurement.

The source-level audit had previously labelled unverified DAO holdouts
`GAIA_HOLDOUT` and counted them as `Tractor-only`, which falsely
suggested independent catalogue-based selection. This metadata bug is
corrected here without relaxing any cuts or changing the fitted PSF.

**Outstanding closure requirement:** run `outer_sky` (8 iterations)
and `outer_sky_convergence` (16 iterations) with exactly the same
explicit `gaia_strict` or `tractor_psf_validated` policy and
unmodified input files, and export full non-null reports for both.
Compare exact training and holdout star IDs and fixed selection
fingerprints, not only the headline residual percentages. Keep this
branch DRAFT and the scientific adoption gate closed.


## New real Leo P i-band diagnostic: 2026-11-05 16:52–16:53 UTC

Two full-griz metadata exports were inspected directly:

- `16:52:40.531Z`: `i` records `outer_sky_robust`
  (`max_iterations=8`, `tractor_psf_validated`, 2 guarded training
  outlier passes), but `science_report_or_rejection=null`. The current
  UI policy was `dao_diagnostic`, contradicting the old
  `exact_result_recipe.star_catalog_policy`. This is a stale
  *provenance* bug in a reset/unfinished report, not an 8-iteration
  scientific result. Original `i`-band image/invvar/maskbits/nexp SHA256
  match the next export.
- `16:53:45.141Z`: the completed `i` result uses
  `outer_sky_convergence` (`max_iterations=16`),
  `tractor_psf_validated`, no train residual auto-trim,
  `PSFSIZE=5.12` guessed FWHM and a `65×65` pixel stamp. All 1427
  DAO detections were screened, 1402 rejected before the sample split:
  **22 training** (10 Gaia-matched + 12 Tractor-PSF without Gaia) plus
  **3 Gaia-matched independent holdouts**. The signed holdout integrity
  report was `COMPLETE` (3/3 stars with core, intermediate and outer
  annuli); none of their IDs was replaced based on fit residuals.
  The selected instrumental source quartet is unchanged from 16:52.

The second run is a useful numerical convergence milestone. Photutils
3.1.1 reached `converged=True` in **12/16** iterations,
`final_converged_fraction=1.0`, `max_center_shift=0.000301 px`;
the last `max_epsf_change` was **0.002732** of the ePSF peak.
These conditions establish the library's *centroid-based*
convergence, not wing fidelity or PSF applicability.

The PSF is still **`STRONG_NEGATIVE_EPSF_RINGING`**:
negative absolute mass **0.053711** versus blocking pilot 0.025.
Of the *total signed-kernel absolute mass*, 0% is negative in the
core, **0.1739%** is negative at 1–2 guessed FWHM and **5.1972%**
is negative outside 2 guessed FWHM; within that last outer region
**40.95% of the absolute ring mass itself is negative**.
The 3 Gaia holdouts have median per-pixel absolute signed-model
residual / stellar peak **0.009757 / 0.003151 / 0.002241** across
the three annuli. These medians do not constrain integrated outer
ringing: pixels with opposite residual signs can offset, the ring
contains thousands of near-sky pixels, coadd noise is correlated, and
the sample contains only 3 independently selected stars.

**Strong population/brightness confound:** all 12 Tractor-only
training sources are significantly fainter than the Gaia training
sources. The two groups have no overlapping measured-peak range:
Tractor-only peaks 0.0798–0.1804 nMgy/pixel versus Gaia 1.371–17.323.
The groups' median peak/RMS is **17.83 versus 484.95** and their
median positive native core moments are **3.182 versus 2.339 px**
(≈36% larger for the faint population). These are *not PSF FWHM*;
at this brightness disparity they cannot establish that the faint
population is nonstellar. Bright Gaia holdouts cannot directly
validate potential population-dependent biases introduced by the
faint Tractor-only training sample. Spatial sampling may also be
different. Do not automatically remove the faint sources based on
an unmatched width or on training-set residuals.

**Required controlled tests before deciding whether the sample or the
method is at fault:**

1. Run `outer_sky` (8) and `outer_sky_convergence` (16), both with
   `tractor_psf_validated`, the same original `i` native FITS,
   `5.12 px` FWHM guess, and no automatic training outlier passes.
   Both runs must have non-null full result QA; compare exact train/
   holdout Gaia IDs and exact effective recipe fields.
2. Repeat the same 8/16 pair with `gaia_strict`, retaining 10+3
   minima. This is a population ablation, **not** a direct fixed-star
   comparison between selection policies. Record selection underflow
   if it occurs; never lower the minimum.
3. Only if necessary, evaluate `wing_smoothing=False` as a **separate
   controlled ablation** at oversampling=1 with a fixed training
   population, because Photutils 3.1 documentation cautions wing
   smoothing may reduce wing fidelity in undersampled ePSFs.
   This is not evidence that smoothing caused the current rejection:
   the existing outer annulus (>2 FWHM) combines the inside/outside
   of the library's wing-smoothing radius.
4. Independently inspect rejected signed ePSF, per-star cutouts,
   2D residuals, annular flux integrals, sky and field-position
   variation. Keep PSF0 non-adopted; do not weaken the 2.5% screening
   heuristic to manufacture a pass.

**Export provenance repair:** an old `_last_reported_payload` could
survive a filter/catalog policy/experiment reset, creating a non-null
`exact_result_recipe` beside `science_report_or_rejection=null`.
The new exporter records `result_provenance_state`, clears stale
recipe state upon reset, and refuses to attach old source hashes or
effective recipe when no scientific result exists. This repairs
report integrity, without modifying the PSF-fitting algorithm, saved
FITS or historical QA files.

Reference: official Photutils 3.1 ePSF builder documentation:
https://docs.astropy.org/projects/photutils/en/stable/user_guide/epsf_building.html


## Real Leo P controlled i-band audit — four files, 2026-11-05 18:15–18:17 UTC

Four full griz metadata exports were supplied, but **only three distinct
scientific configurations** were run. All `i` results use exactly the
same source quartet SHA256, guessed FWHM 5.12 native pixels, original
catalogue hash, 65×65 native stamp, 24–30 pixel sky annulus, 69 pixel
isolation, and the same fixed three Gaia-identified holdouts
(`star-0089`, `star-0059`, `star-0047`). The `g/r/z` source FITS
paths are listed, but no new scientific results for these bands were
computed in these exports.

| Uploaded name | Effective policy and recipe | Photutils build | Negative absolute mass | Scientific status |
| --- | --- | --- | ---: | --- |
| `LeoP_i_Tractor_8iter.json` | `tractor_psf_validated`, `outer_sky`, 8 max, no auto trimming | 8 iterations; `converged=False` | 4.824812% | REJECTED |
| `LeoP_i_Tractor_16iter.json` | **Actually** `tractor_psf_validated`, `outer_sky_robust`, **8 max**, 2 train-screening passes requested | **8 iterations**; `converged=False` | 4.824812% | REJECTED |
| `LeoP_i_Gaia_8iter.json` | `gaia_strict`, `outer_sky`, 8 max | 8 iterations; `converged=False` | 2.133435% | REJECTED FOR NONCONVERGENCE |
| `LeoP_i_Gaia_16iter.json` | `gaia_strict`, `outer_sky_convergence`, 16 max | **9 iterations; `converged=True`** | **2.189109%** | `EXPERIMENTAL_PSF_NOT_ADOPTED` |

The mislabeled Tractor "16" export is **not** a 16-iteration
result. Its signed-kernel metrics and fixed Gaia-holdout residuals are
numerically identical to Tractor 8, because the guarded screening
explicitly blocked train-star auto exclusion due to strong
brightness-residual-score correlation. The only actual recipe
difference was `training_outlier_passes=0` versus 2.

**Valid A/B 1: 8 vs up-to-16 on exactly the same Gaia sample.**
All ten training Gaia IDs and all three holdout Gaia IDs are identical;
`max_iterations` is the sole effective recipe difference. At the
eighth iteration `final_converged_fraction=1.0`, but the common-center
shift was 0.001399 native pixels and the library still reported
`converged=False`. At the ninth iteration the common-center shift was
0.000536 px and the library reported `converged=True`. The extra
iteration therefore resolves this **numerical** convergence deficit;
the previous premature stop must not be counted as a successful fit.

However, negative mass **slightly increased** from 2.133435% to
2.189109%, and heldout median absolute residuals / measured peak
(core/intermediate/outer) changed only from
**0.0073868/0.0028998/0.0021268** to
**0.0075754/0.0028863/0.0021296**. Numerical convergence is **not**
evidence of improved wings or lower holdout residuals. The 16-limit
model produces a real finite normalized 65×65 nonnegative kernel
(SHA256 `ce8f3e9699451d74546d9287bfc4bcfc7c8fdd18b18291530b9f88c6a55241aa`);
it remains experimental and not adopted for science. It sits only
0.3109 percentage points below the **heuristic, uncalibrated**
2.5% negative-mass gate.

**Valid A/B 2: Gaia-only vs Tractor-vetted at eight iterations.**
Only `star_catalog_policy` differs in the recipes: 10 Gaia training
stars + 3 Gaia holdouts are preserved, with 12 additional faint
Tractor-only training candidates in the latter. At exactly eight
iterations the negative mass changes from 2.133435% to 4.824812%
(a 2.26-fold increase). Its contribution outside two **guessed**
FWHMs changes from 2.130724% to 4.700213% of total model
absolute mass. The same three holdouts show core median absolute
residuals increasing from 0.0073868 to 0.0095216 per peak.
This is strong evidence of **sample-composition sensitivity** under
fixed settings, not a proof that the faint Tractor-only candidates are
galaxies: their intensity/SNR ranges are distinct from the Gaia sample,
and low-S/N wing/sky and spatial/centering effects remain confounded.

**Important remaining method gap:** the three-annulus detailed
independent heldout residuals evaluate the *signed pre-clipping*
candidate. For a provisionally successful model, the delivered kernel
is obtained by clipping negatives and renormalizing. Gaia 16's signed
positive mass is 1.022893 and negative absolute mass 0.022893, so
renormalization scales all positive pixels by `1/1.022893 ≈ 0.977619`
before any photometric use. There is a separate single-core-aperture
holdout statistic on the clipped model, but **no equivalent three-zone
per-star validation** of the final nonnegative kernel. The signed
and clipped-core summaries use different apertures and must not be
compared as if identically measured. This unresolved evaluation
mismatch, plus only three independent holdouts, prevents wing-level
science acceptance even for the successful Gaia 16 run.

**Next required evidence and development:**

1. Re-run **one missing controlled configuration**:
   `tractor_psf_validated` +
   `outer_sky_convergence` (16 max,
   `training_outlier_passes=0`), with the same inputs and FWHM.
   Confirm *inside* the exported JSON that the actual
   `exact_result_recipe.max_iterations=16`,
   `experiment=outer_sky_convergence`, policy Tractor; filename alone
   is insufficient.
2. Add per-star **post-clipping, nonnegative, normalized PSF**
   holdout QA on identical native cutouts and identical three radial
   zones, explicitly separated from pre-clipping signed diagnostics
   and without changing current science adoption thresholds.
3. Revisit the wings after checking the raw signed and final kernels,
   with finer annuli around **3.5 guessed FWHM** if studying
   `wing_smoothing`, which Photutils 3.1 applies beyond approximately
   3.5 FWHM. Optional `wing_smoothing=False` must be a separately
   registered fixed-star ablation, not retrospectively treated as
   an explanation for these data.
4. Run all four griz bands and evaluate spatial, sky/PSF degeneracy,
   bright-star wings, independent PSFEx reference and coadd
   covariance before claiming a usable scientific PSF.

Files supplied for this review were not uploaded to the repository.
Their SHA256 digests are preserved here for independent verification:
Gaia 8 `ea9b67fba9cf19d9d941f05e7971ac88fe62e61565c33b3ed44588559903e7af`,
Gaia 16 `bc30bd0fb81c08b71065d7e52b3f2cbe833850efa71cf0ce5fc81aa8a113cf3d`,
Tractor 8 `df9715445248123566d2a5e0a8330461e1fe3df4e67df4b118993a89123d0998`,
mislabeled Tractor 16 `3d3838181e37a239819351b2ea174d59179baa688862701806dedc0b091cdc0b`.


## Completion of controlled Leo P i-band 2×2 matrix — 2026-11-05 18:29:57 UTC

User supplied `LeoP_i_Tractor_16iter_actual.json` generated at
`2026-11-05T18:29:57.259948+00:00` (uploaded file SHA256
`293044497ea9b3de...`). The file **validly closes the missing test**:
`experiment=outer_sky_convergence`,
`star_catalog_policy=tractor_psf_validated`,
`max_iterations=16`, `training_outlier_passes=0`,
`psf_oversampling=1`, `wing_smoothing=true`,
`cutout_size_pix=65`, identical native DR11 `i` FITS and
catalog SHA-256, the **same 22 training** and **same 3 independent
Gaia-identified heldout** star IDs as the Tractor 8 run. Its complete
recipe digest is
`922626248dc6f208b7f45a9c1331e9d3d83be393bc089b091340d8777498e480`.
It reproduces the earlier independently exported real 16:53:45 UTC
Tractor 16 result exactly in its recipe, solver diagnostics,
signed-kernel aggregate and signed-holdout metrics. The previous
invalid filename-labeled `Tractor_16iter` export remains historical
only and is NOT part of this 2×2 comparison.

| Effective policy and max solver iterations | Actual iterations | `EPSFBuilder.converged` | Preclip negative absolute mass | Median signed holdout abs residual / peak, core ; outer | Quality |
| --- | ---: | --- | ---: | --- | --- |
| Gaia strict, 8 | 8 | False | 2.1334% | 0.0073868 ; 0.0021268 | Rejected for nonconvergence |
| Gaia strict, 16 | **9** | True | **2.1891%** | 0.0075754 ; 0.0021296 | Experimental preview, NEVER science-adopted |
| Tractor-vetted, 8 | 8 | False | 4.8248% | 0.0095216 ; 0.0022206 | Rejected, negative mass |
| Tractor-vetted, 16 | **12** | True | **5.3711%** | 0.0097568 ; 0.0022410 | Rejected, negative mass |

The extra iteration budget enables numerical convergence in **both**
fixed-sample pairs, but makes neither sample's negative-mass score
better. In the converged 16-max comparison, adding 12 faint Tractor-only
training sources increases the signed negative absolute mass
**2.4537×** (5.3711/2.1891) and the signed negative absolute mass
*outside 2 guessed FWHMs* from **0.022861 to 0.058227**
(in units of the unit-signed-sum model). This is a controlled
sample-composition effect, **not proof of source contamination**. The
same 3 Gaia holdouts' median absolute signed-core residual increases
from 0.0075754 to 0.0097568 per stellar peak; core residuals increase
for **two of the three** holdouts, and outer residuals increase
for **all three**. The sample is too small to justify a calibrated
statistical confidence level.

**Subtle but critical wing diagnostic:** the signed-integrated outer
mass (r > 2 guessed FWHMs) is nearly unchanged,
**0.024631 Gaia vs 0.025733 Tractor**. However, the Tractor 16
outer annulus contains +0.083960 positive mass and -0.058227
negative mass, versus Gaia 16's +0.047492 and -0.022861. This is
**positive/negative wing compensation**, not evidence that wings are
physically accurate. A hypothetical *clipped and renormalized*
Tractor model would allocate ~**7.92%** of its flux to the
r > 2 guessed-FWHM annulus, compared with ~**4.64%** for the
delivered Gaia kernel (both computed from stored preclip aggregate
masses). The Tractor hypothetical model is **NOT constructed for
science, adopted or written out**. This discrepancy directly motivates
a delivered-model three-zone holdout audit.

**Methodological implementation:** the branch now also calculates
`final_holdout_summary_after_clipping`,
`final_holdout_coverage_after_clipping`,
`final_holdout_details_after_clipping` on the **same fixed
independent stars, native cutout, weighted amplitude fit and three
radial zones** as the signed precursor, but using the final
nonnegative unit-normalized kernel. An incomplete final holdout audit
rejects the result without replacing holdout stars. Added tests.
This changes *diagnostic completeness*, **not** the physical
adoption threshold or the sign-based screening; existing historical
JSONs predate the new fields.

Outstanding for closure: validate the new final-kernel QA with fresh
Gaia-strict 16 export, isolate the effect of Photutils 3.1
`wing_smoothing` beyond ~3.5 guessed FWHMs under fixed sample and
non-adopted status, then test `g/r/z` on their own measured PSFSIZE
maps. Validate PSF wings, spatial variation, noise covariance and
independent external PSF reference before scientific adoption.

## Real Leo P Gaia 16 final-kernel QA — 2026-11-06 15:51:24 UTC

New uploaded export `LeoP_i_Gaia_16iter_finalQA.json` (file SHA256 `74c05445e3a3e868fb969b147e48273aaf62e25cfa2e0ca9af865ef3ef00644e`) contains a **non-null** i-band scientific report, with the same original four source SHA256 values, final kernel SHA256 `ce8f3e9699451d74546d9287bfc4bcfc7c8fdd18b18291530b9f88c6a55241aa`, 10 Gaia train plus 3 fixed Gaia holdout stars. The recipe is `gaia_strict`, `outer_sky_convergence`, max 16, actual 9 iterations, guessed FWHM 5.12 native px, `psf_oversampling=1`, `wing_smoothing=True`; Photutils 3.1.1 `converged=True`. Signed negative absolute mass fraction 0.02189109 remains under heuristic 0.025, but science adoption is **false**.

Both **signed precursor** and **final nonnegative normalized** holdout coverage checks are COMPLETE (all 3 original held-out Gaia IDs, all 3 radial zones, identical number of native valid pixels, no failures). Median per-star absolute residual / observed peak by radial zone is:

| Radial zone | Signed precursor | Final normalized kernel |
| --- | ---: | ---: |
| r ≤ 1 guessed FWHM | 0.0075754093 | 0.0075754082 |
| 1–2 guessed FWHM | 0.0028862779 | 0.0028815222 |
| r > 2 guessed FWHM | 0.0021295936 | 0.0021266960 |

The median **signed** outer-zone residual changes from -0.000048303 to -0.000201420 of measured stellar peak, even though median absolute residuals barely change. That is not a calibrated flux-bias estimate. Also, the positive signed-kernel sum is 1.0228934176. Dividing by that to normalize the delivered nonnegative kernel is **almost perfectly cancelled** by re-fitting each holdout's amplitude upward by 1.02289342 (verified on `star-0089`, `star-0059`, `star-0047`). Therefore invariant core residuals are expected; these results confirm comparison integrity but do **not** prove integrated photometric flux conservation, valid extended wings or an unbiased galaxy SB profile.

**New controlled GUI mode:** `outer_sky_no_wing_smoothing` (fifth experimental recipe), based strictly on `outer_sky_convergence` but with only `wing_smoothing=False`: same max 16 iterations, same native pixel oversampling, cutout, annulus, Gaia selection, fixed holdouts and disabled automatic train trimming. The existing scientific backend refuses disabled smoothing if Photutils does not support the parameter. Photutils 3.1 applies wing smoothing *once after iterations*, primarily beyond ~3.5 FWHM (measured by the library), and cautions that with oversampling 1, smoothing can affect wing fidelity. The option is an A/B investigation, not a PSF-promoting heuristic. Official reference: https://docs.astropy.org/projects/photutils/en/stable/user_guide/epsf_building.html.

**Next Leo P run:** after updating the existing branch, select i-band, `gaia_strict`, `outer_sky_no_wing_smoothing`, guessed FWHM 5.12 px, no manual exclusions. Export full QA. Compare source SHA256, exact 10+3 IDs, solver convergence, negative annular mass, signed and final clipped heldout QA to the existing smoothing-ON export. Preserve historical JSONs and the NON-ADOPTED science state. The g/r/z native-band pilots and PSF spatial/wing/covariance validation remain open.


## Real Leo P controlled wing-smoothing A/B — 2026-11-06

**Complete native DR11 i-band diagnostic.** Compared the real exports
`LeoP_i_Gaia_16iter_finalQA.json` (ON; 2026-11-06 15:51 UTC)
and `LeoP_i_Gaia_16iter_noWingSmoothing.json` (OFF; 2026-11-06
16:06 UTC). The original four FITS SHA-256 digests match byte-for-byte
and the complete effective recipes differ **only** in
`wing_smoothing=True/False`. Both use `gaia_strict`, 10 identical
Gaia training IDs and 3 identical fixed Gaia heldout IDs
(`star-0089`, `star-0059`, `star-0047`), the same detection prior
5.12 native pix, 65x65 cutout, sky annulus 24–30 px, 69 px
isolation, native oversampling 1, no training auto-trim and no
manual exclusions. The entire nine-step Photutils 3.1.1 solver
iteration history is **identical**; both converged after nine
iterations, final stable-center fraction 100%. Only the
*post-convergence* wing-smoothing transformation changes the result.
This cleanly isolates the last-step regularization.

| Signed ePSF diagnostic (fraction of signed-total 1 except where stated) | wing smoothing ON | OFF |
|---|---:|---:|
| Numerical solver convergence / iterations | true / 9 | true / 9 |
| Negative absolute mass / total absolute mass | **2.189109%** | **5.978836%** |
| Diagnostic 2.5% negative-mass gate | passes | **fails** (`STRONG_NEGATIVE_EPSF_RINGING`) |
| Outer r > 2 guessed FWHM positive signed-model mass | 0.047491862 | 0.092505526 |
| Outer r > 2 guessed FWHM negative absolute mass | 0.022860952 | 0.067876184 |
| Outer r > 2 guessed FWHM signed integral | **0.024630910** | **0.024629342** |
| Negative fraction within outer annulus (absolute mass) | 32.495% | 42.322% |
| Three Gaia holdouts median absolute signed-core residual / measured peak | 0.0075754093 | 0.0075754093 |
| Same metric, intermediate 1–2 guessed FWHM | 0.0028862779 | 0.0028862545 |
| Same metric, outer > 2 guessed FWHM | 0.0021295936 | 0.0022535665 |

Removing wing smoothing increases the *negative absolute mass
fraction* **2.731x** (+3.790 percentage points) and nearly doubles
the positive mass outside 2 guessed FWHMs, while the negative outer
mass almost triples. The **net integrated signed wing signal
changes by merely -0.000001568** in unit-signed-sum flux
(≈-0.000157 percentage points). Thus a nearly identical signed
outer flux is a **positive/negative cancellation artifact**, not an
independent measure of PSF wing correctness. All three fixed
holdouts' outer-zone median absolute residuals increase with
smoothing OFF (median +5.82% in relative terms), but the three
stellar images and coadd residual noise are insufficient to
calibrate physical wing fidelity.

These results show that Photutils' final `wing_smoothing=True`
regularizes the noisy oscillatory outer ePSF for this particular
ten-star field. They do **not** establish that the smoothed model is
an accurate physical representation of bright-star wings:
post-convergence smoothing may remove real high-frequency structure;
the model's halo is degenerate with local-sky estimation; spatial
variations and coadd covariance are not validated. Photutils 3.1.1
applies wing smoothing mainly at >3.5 **measured** FWHM, whereas
Desidera's per-annulus QA here partitions at 1 and 2 **guessed**
FWHMs. The exported JSON contains neither the native star stamps
nor fine annuli around the smoothing transition, so this particular
experiment cannot isolate the radial transition further.

**Scientific outcome:** OFF is rejected, and **no positive kernel
was delivered for that failed run**: the absence of
`final_holdout_*_after_clipping` is expected, not missing QA
for a successful model. ON has complete signed and post-clipping
three-zone holdouts, but remains
`EXPERIMENTAL_PSF_NOT_ADOPTED`.
Do not lower the 2.5% diagnostic screen to rescue OFF and do not
claim a calibrated acceptance from that screen.

**Decision / next gates:** retain ON solely as the provisional
experimental `i` reference, stop unmotivated iterations/wing-tuning
for Leo P, and survey `g/r/z` at their **own** PSFSIZE priors.
Independently evaluate actual stellar cutouts, 2D signed residuals,
matched radial enclosed-energy profiles, sky-annulus sensitivity,
spatial PSF representativity, calibrated injection recovery and
coadd-noise covariance before PSF science adoption. The three-band
pilot can close the PSF-0 exploratory milestone even when an
honestly rejected band stays rejected, provided the failure is
reproducible and clearly recorded.


## Full Leo P native griz pilot — 2026-11-06 16:20–16:22 UTC

The three independently generated full QA exports from the user
(`LeoP_g_Gaia_16iter_finalQA.json`,
`LeoP_r_Gaia_16iter_finalQA.json` and
`LeoP_z_Gaia_16iter_finalQA.json`) were parsed completely, including
the full candidate rejection inventories and individual heldout-star
metrics. Each has an explicit correct band-level dataset
(`dr11/south/1554p180/{band}`), `gaia_strict`,
`outer_sky_convergence`, `max_iterations=16`,
`wing_smoothing=True`, `psf_oversampling=1`,
`training_outlier_passes=0`, and no manual excluded stars.
FWHM guesses come independently from the local native-band PSFSIZE
map (NOT from measuring the fitted ePSF). The top-level
`saved_dataset` remains `g` for all exports, whereas
`bands.r.source_dataset`, `bands.z.source_dataset`, the science
dataset IDs, and their respective native-FITS hashes correctly
identify `r` and `z`. This is a distinction between the saved
project's selected source and each band-specific PSF job;
**never use the top-level saved_dataset as science-run provenance**.

| Band | Guessed FWHM (native px) | Gaia accepted | Training / fixed independent Gaia holdout | EPSFBuilder actual iterations and convergence | Signed negative absolute fraction | Status |
|---|---:|---:|---|---|---:|---|
| g | 6.57 | 10 | No split — 10 vs required 13 | **not fitted** | — | `stellar_sample_underflow`, fail closed |
| r | 4.88 | 20 | 16 / 4 | **6 / true** | **1.235216%** | `EXPERIMENTAL_PSF_NOT_ADOPTED` |
| i | 5.12 | 13 | 10 / 3 | **9 / true** | **2.189109%** | `EXPERIMENTAL_PSF_NOT_ADOPTED` (prior controlled 2026-11-06 run) |
| z | 3.99 | 49 | 39 / 10 | **4 / true** | **1.430110%** | `EXPERIMENTAL_PSF_NOT_ADOPTED` |

In `g`, **all 972 DAO-detected sources were screened**, with
10 Gaia-identified sources passing all cuts; the explicit minimum
is 10 training + 3 **independent Gaia holdout** sources, so no fit,
holdout, clipping, or PSF science measurement was produced. Exactly
recorded first-failure counts:
`NOT_GAIA_DR3=694`;
`TRACTOR_NEIGHBOR_WITHIN_PSF_CUTOUT=109`;
`NO_TRACTOR_SOURCE_AT_DAO_CENTROID=109`;
`NEIGHBOR_WITHIN_ISOLATION_RADIUS=27`;
`TRACTOR_NONSTELLAR_MODEL=18`;
`AMBIGUOUS_TRACTOR_CROSSMATCH=4`;
`GAIA_DUPLICATED_SOURCE=1`.
The screening audit includes 193 `NOT_GAIA_DR3` first-failure
sources with a Tractor `TYPE=PSF`, but these have **not**
passed the remaining Tractor-vetted image/cutout and isolation
screens: this number is not a promise of 193 usable stars.
A targeted `g`-band `tractor_psf_validated` run can probe
whether ≥10 training plus ≥3 Gaia-identified fixed holdouts can be
obtained **without weakening cuts**. The i-band evidence shows faint
Tractor-only stars can destabilize the wings, so a successful sample
size alone does not prove a usable g PSF. Preserve full rejection
and signed/holdout QA, and do not promote Tractor-only sources to
Gaia identity.

In `r` and `z` both initial signed and final clipped,
unit-normalized **three-zone** residual QA return `COMPLETE`
for all fixed Gaia holdouts: no discarded IDs, no incomplete
annuli. The final kernel flux sums are 1 within numerical precision;
their kernel SHA256 are `dabeda624b95467af85327cbfa962cd487d44e396af82759633fa93443b19269`
for r and `9e17e229993298dd6c7ea38f078eb8bc358087c40e2323d3875882af4b73594c`
for z. In every band the library flags **unvalidated wings, spatial
representativity, coadd covariance and sky-wing degeneracy**.

| Final nonnegative kernel holdout metric (median of each fixed star's median absolute residual / measured peak) | r (4 Gaia holdouts) | i (3 Gaia holdouts) | z (10 Gaia holdouts) |
|---|---:|---:|---:|
| Core ≤1 guessed FWHM | 0.565853% | 0.757541% | 2.127111% |
| Intermediate 1–2 guessed FWHMs | 0.078916% | 0.288152% | 0.760707% |
| Outer >2 guessed FWHMs | 0.054135% | 0.212670% | 0.253799% |

These summaries are **not calibrated chi-square or integrated flux
bias**, and the band-specific holdout stars, SNR, backgrounds and
PSF FWHMs differ: do not treat this table as a controlled test of
relative band PSF accuracy. `r` has one moderate outer-residual
outlier, `star-0077` at 0.455% of its peak (other three:
0.058%, 0.050%, 0.024%). In `z`, four low-peak/RMS
holdout stars have >1% outer residual: `star-0151`
(1.026%, peak/RMS≈64), `star-0246` (2.506%, ≈26),
`star-0293` (2.624%, ≈26) and `star-0431`
(4.061%, ≈17.5). The brighter six z holdouts (peak/RMS
≥100) have an outer median ≈0.194% compared with ≈2.565%
for the four faint stars. Their percentage residuals are normalized
to *peak* and are therefore strongly SNR-confounded; these numbers
alone do **not** show spatial PSF variation, stellar contamination
or a defective z-band kernel. All four should be inspected on raw
native coadd stamps with the local sky, weight/mask and residual
images. The halo/sky annulus still lacks a joint covariance-aware
fit.

**Pilot decision:** the griz *diagnostic coverage* is now complete
(including an honestly recorded g-band refusal). Do not merge
into science adoption on the basis of 3/4 provisional kernels.
Perform **one controlled g Tractor-vetted 16-max trial** with the
existing per-band PSFSIZE 6.57 px, fixed Gaia holdouts and unchanged
minimums (10+3); retain the original g Gaia-strict refusal as
a stable reference. Then close exploratory PSF-0 as a diagnostic
milestone, or leave the g-band refusal explicitly unresolved if
Tractor also fails. Any move to *scientific* PSF use requires a
separate forward-model / PSFEx / injection and covariance-calibrated
wing-and-spatial-validation stage, including r/z outlier inspection.
Do not modify original FITS, current quality gates or star identity
policies to manufacture passes.

File SHA256 for provenance (the huge JSONs themselves are not
committed to the repo): g
`aab6c83b05fbcb129edebdb7fe5105450de9a5fb1ef3f09ce291c195190e7056`,
r `d2b2b0e1476deadad67aabea9cea6bbcf1e5e22a7a7afbfa7ccbe8a62ef64cbf`,
z `2500094c5607722e14aa9a753a10b6b585e9243cf8361867c1018f6a92396985`.


## Final Leo P g-band Tractor-vetted control — 2026-11-06 16:46 UTC

User-provided complete QA export: `LeoP_g_Tractor_16iter_finalQA.json`,
created `2026-11-06T16:46:20.437544+00:00`; file SHA256
`2505cf221256be211e5dbf1969eb422762d3a581fff4b9f8332d8a1f966d3ec5`.
Comparative reference: the earlier Gaia-strict g export at 16:20 UTC,
SHA256 `aab6c83b05fbcb129edebdb7fe5105450de9a5fb1ef3f09ce291c195190e7056`.
The DR11 south `1554p180` g `image/invvar/maskbits/nexp`
SHA256 hashes and original Tractor catalogue SHA256
`8e8e3f75af868f3ef7cf4c4c9d7ca28210cbca87ef27c98351a6ab237010562c`
match exactly. Both effective recipes differ **only** by
`star_catalog_policy`, `gaia_strict` versus
`tractor_psf_validated`; the following are fixed: 6.57 native-pixel
PSFSIZE detection prior, 81×81 native cutouts, 30–38 pixel local-sky
annulus, 85 pixel DAO isolation, `outer_sky_convergence`,
16 max iterations, `wing_smoothing=True`, minimum
**10 train + 3 independent Gaia holdouts**, 0 training outlier
passes, zero manual exclusions and native oversampling 1.

| g-band selection diagnostic | Gaia-strict | Tractor-vetted |
| --- | ---: | ---: |
| DAO candidates found / screened / unexamined | 972 / 972 / 0 | 972 / 972 / 0 |
| Accepted before field-relative core-width screen | 10 | 12 |
| Accepted after all image/catalogue screens | 10 | **11** |
| Gaia-identified holdout-eligible stars | 10 | **10** |
| Tractor-only accepted | 0 | **1** |
| Required accepted total | 13 | 13 |
| EPSFBuilder executed? | No | **No** |
| Final status | `stellar_sample_underflow` | **`stellar_sample_underflow`** |

The 10 Gaia-identified accepted stars are *identical* in the
two runs. The Tractor policy adds only
`star-0354`, Tractor OBJID 2022 with **no Gaia DR3 identity**,
native peak `0.0366293 nMgy/pixel`, local RMS
`0.00346491 nMgy/pixel` (**peak/RMS ≈10.57**),
and positive-core moment sigma 4.1521 px versus the Gaia
sample's reference median 3.1563 px (**ratio 1.316**).
The accepted additional star has very low S/N compared with
Gaia's median `peak/RMS≈407.82`. Its acceptance does **not**
certify a Gaia identity, a correctly modeled PSF wing, or a
brightness-matched comparison.

A second Tractor-only candidate `star-0541`, Tractor OBJID
2846, passes the upstream catalogue and image-shape checks,
but is rejected by the *heuristic field-relative width* screen:
its positive-core moment sigma is 5.1149 px, a ratio
**1.621** to the Gaia median, exceeding the 1.45
maximum. Its native peak/RMS ≈9.09. The diagnostic's current
first-failure label `GAIA_STELLAR_CORE_SIZE_OUTLIER`
indicates a width screen **calibrated on Gaia stars**, **not**
that `star-0541` is Gaia-identified; the label should be clarified
in a future schema version. This weak source would not resolve
the g-band underflow even if admitted: 12 < 13. **Do not alter
the width criterion to force a pass.**

Tractor-vetted first-failure counts (exactly **961** total
rejections after the 11 accepted candidates):
`TRACTOR_NONSTELLAR_MODEL=519`,
`TRACTOR_NEIGHBOR_WITHIN_PSF_CUTOUT=195`,
`NO_TRACTOR_SOURCE_AT_DAO_CENTROID=109`,
`TRACTOR_LOW_BAND_SIGNAL_TO_NOISE=100`,
`NEIGHBOR_WITHIN_ISOLATION_RADIUS=32`,
`AMBIGUOUS_TRACTOR_CROSSMATCH=4`,
`GAIA_DUPLICATED_SOURCE=1`,
`GAIA_STELLAR_CORE_SIZE_OUTLIER=1`.
These are **first** failure labels, not independent astrophysical
populations or evidence that all 519 sources are actual galaxies.
With three Gaia holdouts reserved, the current 10 Gaia plus
one Tractor-only candidate leave 8 training stars, 2 short
of the required 10. No numerical ePSF iteration, PSF kernel,
negative-ring statistic, signed or clipped holdout residual,
science-model or photometric measurement exists for `g`.
The refusal is therefore an **expected correct fail-closed result**,
not a bad solver fit.

**Pilot conclusion, 2026-11-06:** all four native griz
bands now have diagnostic coverage, including a reproducible
sample-size refusal under **two distinct g-band selection
policies**, plus provisional non-adopted reconstructed ePSFs
for r/i/z. There is no justification for retrying the same
g case with more solver iterations or for weakening minimum
train/holdout, neighboring-source, band S/N, width,
and Gaia identity screens. This **closes the present
exploratory Leo P PSF-0 test matrix**, with g explicitly unavailable.
It **does not close science readiness** or authorize merging
the PSF as an adopted photometric or SBF kernel. Subsequent
scientific work should build a physically defensible g fallback
(e.g. independently benchmarked survey/PSFEx source model or
neighbor-aware cross-field/visit stacking with proper coadd
provenance) and calibrate actual r/i/z wing fidelity,
spatial variation, background degeneracy, full flux-encircled
profiles, injection recovery and noise covariance. Treat
a substitute g PSF as a new method with its own QA.


## PSF-1 validation groundwork: native core-normalized holdout flux growth

The post-pilot method now adds **radial cumulative-aperture flux-growth
diagnostics on the same frozen independent stars and native DR11
coadd pixels** used in PSF-0. This is an intermediate validation
instrument, **not** a new science-ready PSF or a correction of the
known g-band sample underflow.

For each heldout star, fit the model amplitude by the existing
weighted core-only procedure, but additionally compare aperture
growth at `R/FWHM_guess = [1, 1.5, 2, 3, 4]`.
Let `I` be the *already locally sky-subtracted* original
native-image cutout, `P` the subpixel-shifted signed or delivered
nonnegative model on the **identical** pixel grid, and `G` the
original nonzero-weight validity mask. Define

```text
O(R) = sum_{r <= R, G} I / sum_{r <= FWHM_guess, G} I
M(R) = sum_{r <= R, G} (A P) / sum_{r <= FWHM_guess, G} (A P)
E(R) = O(R) - M(R)
```

Here `A` is the pre-existing core-fit amplitude. It cancels
algebraically in the model's own core normalization. **E(R) is a
dimensionless cumulative growth excess relative to the stellar
core, not an encircled-energy fraction of the true total stellar
flux.** Such a total cannot be inferred from the finite local-sky
subtracted cutout. Each aperture inherits local-sky errors and
unknown correlated-noise covariance. Cumulative `E(R)` at
different radii are themselves strongly correlated and cannot
be treated as independent measurements.

Each fixed holdout report now carries
`core_normalized_aperture_growth`, including native radius,
observed/model cumulative-core flux ratios, signed excess,
valid-pixel count, geometric containment and mask coverage.
`FULL_NATIVE_APERTURE` means geometrically contained and no
masked pixels; only those points enter
`signed_holdout_core_normalized_flux_growth_summary` or
`final_holdout_core_normalized_flux_growth_summary_after_clipping`.
Mask-truncated or cutout-truncated disks are recorded explicitly
with `MASKED_APERTURE_DIAGNOSTIC_ONLY` /
`NOT_FULLY_CONTAINED` and excluded from their respective
per-radius ensemble medians. If positive core flux is unavailable,
the growth metric remains missing instead of manufacturing an
inferred normalization. This QA never modifies science pixels,
candidate selection, existing holdout validation or the
quality-adoption gate.

Additional cross-validation still required before physical PSF
acceptance: fit realistic local sky simultaneously with outer
stellar wings; inspect observed vs model 2D cutouts; stratify
independent stars by flux/SNR, colour and position; derive
covariance-aware error bands by injection/recovery on real coadd
backgrounds; compare `r/i/z` against independently reconstructed
external *coadd-equivalent* PSFs. Only then is a scientific wing
accuracy threshold defensible.

**Why a naïve PSFEx fallback would be incorrect:** the official
Legacy Survey documentation distinguishes PSFEx models calibrated
for **individual CCD exposures**, used by Tractor's single-epoch
fits, from `legacysurvey-...-image-{band}.fits.fz` **coadds**
created by Lanczos-3 resampling. The `psfsize-{band}` coadd map
is a weighted-average PSF **FWHM** in arcseconds, not a
2D spatially varying PSF kernel. Direct use of one CCD's PSFEx
array, or a Gaussian with the `psfsize` FWHM, cannot certify a
coadd-effective PSF. A defensible future `g` fallback must
identify the contributing CCDs, their calibrated PSFs,
astrometric transformations, per-pixel inverse-variance weights,
resampling kernel and masking, assemble an **effective coadd PSF
at the target position**, and perform its own external
independent-star/injection validation. In the absence of these
inputs it must return `REFERENCE_UNAVAILABLE`, **not**
quietly replace the rejected empirical g PSF.

Authoritative data definitions:
- https://www.legacysurvey.org/dr11/files/
- https://www.legacysurvey.org/dr10/description/

**Deployment verification:** the new code is unit-tested for
exact profile recovery on a noiseless input, excess wing light
detected outside the core, excluded masked/truncated disks,
immutable input arrays and identical signed/final aperture
geometry. A real Leo P follow-up is needed to populate these
new fields: historical griz JSON exports do not retroactively
contain them. The pilot is considered exploratory-diagnostic
complete, but science readiness remains **NOT ADOPTED**.


## Researcher-mandated all-band empirical PSF gate and targeted g rescue

**Non-negotiable scientific requirement (2026-11-06):**
every filter used in scientific surface photometry must have an
**observed-star-derived, scientifically validated empirical PSF**.
For Leo P `g/r/i/z` this explicitly includes `g`:
**3/4 experimental models and a reproducible g refusal are not
all-band scientific readiness**. The prior completed *diagnostic*
experiment matrix is historical; the **PSF development task remains
OPEN until a valid g empirical PSF exists and all four filter
kernels pass physical QA**. PSFSIZE-Gaussian, CCD PSFEx alone,
transfer from `r/i/z`, or an analytic radial shape must never be
adopted as a substitute for observed `g` stellar evidence.

### Real Gaia candidate audit — why the g sample can grow

From the actual full
`LeoP_g_Tractor_16iter_finalQA.json` catalogue audit,
**27 Gaia-confirmed Tractor TYPE=PSF sources** are rejected at
the *first* `NEIGHBOR_WITHIN_ISOLATION_RADIUS` gate while
having zero significant Tractor neighbour matches. In particular,
**four high-SNR Gaia stars** have nearest DAO centroid separations
within the narrow 81–85 native pixel window:

| Source ID | Nearest DAO detected source separation [pix] | Diagonal Tractor S/N proxy | Significant Tractor neighbours |
|---|---:|---:|---:|
| `star-0004` | 81.8953 | 1991.93 | 0 |
| `star-0011` | 82.7593 | 1351.74 | 0 |
| `star-0050` | 82.8233 | 438.40 | 0 |
| `star-0079` | 84.5393 | 235.37 | 0 |

They were rejected **before** the downstream native-image
sky/core-width/cutout checks. They are therefore **potential
additional training/holdout stars, not verified clean samples**.
The nearest DAO detections may be faint noise, true stars or
unmodelled sources; zero significant Tractor neighbours is not
proof of absent contamination.

With the existing g FWHM 6.57 native pixels, the *independent*
extended-sky recipe has `cutout_size=81`, annulus 30–38 px,
and the previous DAO isolation radius 85 px (`size+4`).
A new **registered controlled diagnostic**,
`outer_sky_isolation_footprint`, changes **only** the
DAO centre-to-centre separation criterion to the **full stamp
width (81 px)**. This remains greater than two 38-pixel sky
annulus outer radii, so the nominal circular sky *footprints*
around star and nearest DAO neighbour do not overlap.
It does **not** guarantee there is no PSF-wing contamination
or ensure clean 2D square stamps. All other source identity,
Tractor, masks, unsaturated core, local-sky shape, width
and numerical convergence checks remain unchanged.
Independent Gaia holdout and minimum **10 train + 3 Gaia
holdout** remain mandatory, and any negative-mass failure
is still rejected.

Under an ideal no-additional-rejection outcome, the four new
candidates would raise the Gaia sample 10→14, potentially
allowing 11 train + 3 independently heldout Gaia stars.
**This is not a prediction of the actual number that will pass**.
The real experiment is required. Its effective recipe, current
catalogue/observational hashes and holdout identities must be
reported. If it fails, the next route is vetted per-source
neighbour modelling/segmentation with target native pixels,
then a properly reconstructed observed-star empirical
multi-exposure/coadd PSF. No automatic relaxation and no
analytic fallback.

The future all-band science entry gate
`require_empirical_psf_coverage` requires, in **every** requested
band, an internally verified science receipt with exact
observation/band identity, genuinely star-derived origin,
independent stellar QA, wing/spatial/sky/covariance validation
and adoption. This is a **planning/entry contract** for a
not-yet-implemented scientific photometry executor, not an
assertion that any current PSF-0 product already passes.


---

## P14A — numerical-parameter provenance and observational sensitivity (2026-11-06)

**Scope:** diagnostic engineering implementation on this existing PR, not a
new PSF recipe, a widened stellar sample or an adopted science product.
The previously recorded Leo P g rejection remains a genuine rejection,
and the old 81-vs-85-pixel experiment remains a *controlled exploratory
test*, not a measurement-derived isolation rule.

### What is and is not scientifically justified

The PSF-0 configuration currently contains hard numerical choices whose
values have not been established by controlled original-image injection or
photometric bias criteria. The attached
\`src/lsbpipe/photometry/psf_parameter_audit.py\` enumerates **every
PSF0Config field** and carries a separate traceable snapshot of selected
hardcoded conditions outside that config. Categories distinguish an
observational prior (whose actual map-vs-manual provenance cannot be
deduced from the config alone), a derived geometric value, a heuristic,
an algorithmic policy and a computational budget. All are explicitly
**NOT SCIENCE CALIBRATED**. This registry is an auditable snapshot, not
yet an automatically enforced single source of truth for every constant.

For g, the native 6.57-pixel FWHM from the PSFSIZE prior does *not* by
itself justify the selected 4.5–5.75×FWHM annulus. The 30–38-pixel ring,
81×81 stamp, extra two-pixel border and 81/85-pixel isolation derive
from that uncalibrated choice. A centre-to-centre separation of 81 pixels
does prevent two circles of radius 38 pixels from overlapping, but it
does **not** prevent arbitrary orientations of the 81×81 square cutouts
from overlapping, nor prove the absence of wider stellar wings.
The new geometry audit exposes both conditions separately.

### New observational audits, with no changed science cuts

1. **Native ring sensitivity**: each accepted _star_cutout now repeats
   robust planar fits on original, unsmoothed, non-sky-subtracted pixels
   in two interior *diagnostic* annuli (60–80% and 80–95% of the recipe
   sky inner radius), using the same original valid-pixel mask and
   sigma-clipped plane fitting. The output records their sky-at-centre
   differences in nMgy/pixel, in units of the original local RMS, and
   the implied change of a uniform-offset subtraction in a one-guessed-
   FWHM aperture. These alternative *probe* ring fractions are also
   heuristic: they are **not** candidate replacement sky rings. The
   refit cannot determine whether differences reflect PSF wings or
   genuine spatial sky changes; the outer region beyond the original
   star stamp remains untested. The coadd noise correlation is not
   estimated. No sky model, star, clipping or acceptance gate is
   changed by this audit.

   Additionally scan **every integer native-pixel inner boundary**
   from ceil(guessed detection FWHM) through the configured sky inner
   edge with the original sky outer edge held fixed.  Every fit status
   is retained, and the resulting `one_pixel_inner_boundary_sensitivity_scan`
   is a nested, correlated *sensitivity curve*, not an independently
   sampled uncertainty distribution or a new optimized sky annulus.
   The one-pixel step is the original measurement grid, not another
   arbitrary FWHM fraction. It still cannot find a physically unbiased
   background or probe outside the observed stamp.

2. **Catalogue-first *discovery audit***: for an exact native
   Tractor/Gaia catalog, examine original Tractor TYPE=PSF, REF_CAT=G3
   candidate positions within the usable native FITS stamp and outside
   the target protection mask, apply the existing catalogue-only
   \`match()\` preflight, and compare each position against DAO
   centroids. Report Gaia sources that pass *catalogue-only*
   screening but lack a DAO centroid within the current 2.5-pixel
   Tractor-match radius. This \`n_catalog_preflight_without_near_dao\`
   does **not** indicate that the source passed native pixel, neighbor,
   sky or morphology QA. Such sources are **never admitted** to either
   train or holdout by this change. The audit retains catalog rejections
   and Gaia IDs for independent inspection. The DAO stage and its
   original stellar selection remain unchanged.

   Why: Photutils 3.0+ \`DAOStarFinder\` defaults to
   \`min_separation=2.5*FWHM\` if unset. Because current PSF-0 does not
   explicitly set that option, a source can be missing from the
   DAO inventory despite having an exact catalog position and band
   flux. A crossmatch with a nearby DAO centroid alone does not prove
   that they identify the same physical object. No catalog-selected
   PSF reconstruction is justified until a separate forced-centroid
   route and its native-stamp tests are implemented.

3. **Parameter-provenance report**: every successful experimental PSF
   report now includes \`parameter_provenance_qa\`, and full
   \`stellar_sample_underflow\` diagnostics include it too.  The
   existing per-star \`sky_sample_qa\` acquires
   \`inner_ring_sensitivity_diagnostic\`. The matched Tractor-vs-DAO
   diagnostic is included under \`gaia_catalog_seed_vs_dao_qa\`.
   None of these fields is a new pass/fail score.

### Reproducibility and scientific restraint

- Changed diagnostic implementations are recorded in the effective
  method fingerprint; source hashes, original input quartet and
  training/heldout split are unchanged.
- At the 81-pixel g stamp there is no longer-radius sky information
  outside the square: interior sensitivity is useful, but cannot
  validate extended PSF wings or an uncontaminated absolute sky.
- A "narrower ring" giving a smaller residual is **not** an objective
  to optimize on the same holdout stars; avoid selection leakage.
- The next *scientific* step is to measure background-versus-wing
  bias with larger native fields, multi-brightness original-star
  modeling and spatially separated blind validation, then propagate
  the uncertainty into simulated galaxy surface-photometry recovery.
  Only then can radius, SNR, isolation and star-count requirements be
  chosen from a predeclared error budget rather than fixed constants.

References:
- Photutils 3.1.1, DAOStarFinder, including its default
  \`min_separation\` and threshold scaling:
  https://docs.astropy.org/projects/photutils/en/stable/api/photutils.detection.DAOStarFinder.html
- Photutils 3.1.1, effective PSF construction, background and wing
  caveats: https://docs.astropy.org/projects/photutils/en/stable/user_guide/epsf_building.html
- Liu et al., wide-angle PSF and joint sky/star modelling:
  https://ui.adsabs.harvard.edu/abs/2022ApJ...925..219L/abstract

**Outstanding local original-FITS execution:** the remote GitHub
environment does not contain Leo P original quartet or original
Tractor catalog. The new diagnostics must be generated on the user's
existing Leo P project before any numerical interpretation of their
effectiveness. Both r/i/z kernels remain NON-ADOPTED.


### P14B — real Leo P g pilot: holdout sky-offset equivalence (2026-11-07)

The user-supplied full-window original-data QA
(\`Desidera_LeoP_PSF0_griz_debug_20261106_215758.json\`, stored
outside this source tree) confirms that the g pilot reaches
**10 Gaia-trained and 3 independent Gaia holdouts**, converges after
**11** iterations, and reports signed negative absolute mass
**1.6538%**, but is correctly marked **NOT SCIENCE ADOPTED**.
The other bands were not rebuilt within that debug session.
The three fixed holdout growth differences at 4×assumed FWHM are:
- star-0110 +0.17506 core-normalized units;
- star-0032 −0.03218;
- star-0148 −0.09587.

These three offsets are neither a 17.5%, 3.2%, 9.6% error in stellar
*total* flux nor independently established PSF discrepancies: all
apertures are measured on **already locally sky-subtracted** coadds,
and the sky zero point can mimic an integral growth difference.
The g input has no independent sky or covariance estimate beyond the
original annulus heuristic.

The code now records, per complete unmasked independent holdout and
growth radius >1 guessed FWHM, the value of a hypothetical *uniform*
residual sky offset needed to algebraically reconcile the observed and
modeled **core-normalized** cumulative fluxes. For observed core
\`C\`, aperture flux \`A\`, model growth \`q\`, and valid pixel
numbers \`N_C\` / \`N_A\`:

    delta = (A - q*C) / (N_A - q*N_C)

The native signed and delivered final-kernel QA both report
\`equivalent_uniform_sky_offset_nMgy_per_native_pixel\` and
\`equivalent_uniform_sky_offset_over_local_pixel_rms\`.
At the 4×FWHM radius these would correspond, for the *actual reported
g pilot*, to approximately +0.133 / −0.245 / −0.053 per original
local **pixel** RMS for star-0110 / star-0032 / star-0148.
These **are not statistical significance values**: coadd pixels have
correlated noise and background-plane estimation uncertainty; the
effective sky offset could be spatially nonuniform. Distinct radii
also imply correlated, possibly mutually inconsistent corrections.
No source pixels, fitted PSF or original selection are changed.

The per-star original-pixel interior ring audit also shows a
brightness-dependent signal, especially for the brightest g train
star (star-0002): inner 18–24 px annular plane sky is
0.00943 nMgy/pix above that fit to the adopted 30–38 px ring,
or 2.92 local pixel RMS. This is consistent with the expected
possibility of stellar-wing light contributing to inner annuli,
but it does not establish that the outer adopted ring is unbiased.
The catalog-first preflight finds 38 candidate Gaia/Tractor PSF
sources, 37 near DAO centroids and only **one catalog-only seed**
without a nearby DAO centroid. DAO incompleteness is therefore not
the principal demonstrated cause of the small final g sample here.
The dominant catalog-only preflight rejections are **115 neighbors**,
not the one missing DAO source. The original cutout QA retains the
central-mask rejection of star-0004 while stars 0011, 0050, 0079
are now train-eligible; none of these is evidence of PSF science
adoption.

The scientific next stage must independently constrain the local
background and coadd noise covariance and reconstruct true wide-angle
stellar wings before calibrating fit thresholds, including an
unmodified blind sample and galaxy injection-recovery. Avoid assigning
a new pass/fail threshold based on these same 3 holdouts.


### P14C — repeat g preview, radial sky-offset consistency (2026-11-07)

The follow-up *single-band* original-data export was provided as
`Вставленный текст(1).txt` (JSON; kept outside source and not checked
into Git). It has the **identical** original FITS quartet, recipe
digest `de8141c629b03d95b60e0b05670d775d78a832f2f0b05e6c43788c8647dcd719`,
signed and final model kernel SHA
`8d976b91b4760fc8c51f607ccaf1cc59d4cac622c4c9992e5df11044bed2852a`
and selected star IDs as the preceding 2026-11-06 g preview.
Only the method QA/fingerprint changed; this is a reproducibility
crosscheck, not a new PSF fit or a new independent validation sample.

Although the previously introduced *algebraic uniform-sky equivalent*
at 4×assumed FWHM is modest, its value varies substantially with
radius (1.5, 2, 3, 4× the assumed detection FWHM). Values below are
unitless fractions of each star's **local pixel RMS**, **not**
sigma significance:

| Gaia holdout | 1.5× | 2× | 3× | 4× | Max-min (RMS units) |
| --- | ---: | ---: | ---: | ---: | ---: |
| star-0110 | +0.637 | +0.441 | +0.216 | +0.133 | 0.505 |
| star-0032 | -2.491 | -1.433 | -0.503 | -0.245 | 2.246 |
| star-0148 | -0.047 | -0.126 | -0.043 | -0.053 | 0.083 |

Under the restricted hypothesis of an exact fixed model profile,
an exactly uniform unmodeled additive sky, noiseless observations and
no star contamination, each aperture must imply the same sky offset.
The observed variation highlights why a single 4×FWHM offset is
inadequate evidence that background errors explain the profile
discrepancy. It does **not** statistically falsify constant sky in
the real observations: all apertures are cumulative/correlated, sky
uncertainty and the coadd covariance are unmodeled, and the weak
holdout star samples may carry other biases. Two holdout peak/local
pixel RMS ratios are only about 40 and 44, while the third is ~539.
The median peak brightness of the 10 train stars is 1.142 nMgy/pix,
vs 0.145 nMgy/pix for the three holdouts (~7.86× contrast); the
bright/faint PSF transfer is not calibrated.

P14C adds the per-holdout
`radial_sky_equivalence_consistency` QA object with the number and
radii of complete unmasked apertures, absolute range of the
algebraically implied sky offsets, and the same range divided by
the **local pixel** RMS. No arbitrary radial-consistency threshold
or chi-square is introduced. For 0–1 valid radii, the span is absent
and the status is `INSUFFICIENT_COMPLETE_APERTURES`; masked
apertures never supply a purported unbiased sky estimate.
A compact **median** of holdout spreads is shown in the existing
PSF quality GUI, with full per-star values in Copy QA. Original
training/holdout samples, input pixels, fitted PSF, science status,
and sky annulus remain exactly unchanged.

The next scientific improvement is a joint stellar profile +
spatial sky + known-neighbor fit on **larger original coadd regions**
with an explicit coadd covariance model and independent sky pixels.
This must be tested on heldout stars not used to tune hyperparameters,
and then via galaxy injection/recovery. The present 3 Gaia holdouts
are insufficient to establish science thresholds.


### P14C original-image rerun verification (2026-11-07)

A third user-exported g-only JSON (`Вставленный текст(2).txt`;
**2,148,488 bytes**, not stored in Git) has been independently
parsed. Its core facts are: exact dataset
`dr11/south/1554p180/g`, identical recipe digest
`de8141c629b03d95b60e0b05670d775d78a832f2f0b05e6c43788c8647dcd719`,
identical native 81×81 kernel SHA256
`8d976b91b4760fc8c51f607ccaf1cc59d4cac622c4c9992e5df11044bed2852a`,
10 Gaia train stars, 3 Gaia holdout stars, 11 performed iterations,
1.6538% preclipping negative-mass fraction and
`EXPERIMENTAL_PSF_NOT_ADOPTED`. The new implementation fingerprint
is `0dc1003783281b794cab7bcaba912e72503edbf9f68f313fbd7a3f86c82da16c`,
reflecting the added diagnostics rather than a different fitted PSF.

The new `radial_sky_equivalence_consistency` fields are **present**
and complete at all four noncore radii for every fixed Gaia holdout:
`star-0110` = 0.5045804033,
`star-0032` = 2.2461123578,
`star-0148` = 0.0829508013,
with median 0.5045804033, all in local **single-pixel RMS**
units. This confirms P14C QA serialization and prior predictions on
the original coadd. It is **not** new independent star evidence, a
measured sky uncertainty, or calibrated rejection of the constant-sky
hypothesis.

The 3 heldout stars span large brightness differences:
observed (locally sky-subtracted) peaks are 0.1454, 1.7048,
0.0992 nMgy/pixel. The nearest Tractor catalog source to the
brightest heldout star `star-0032` is about **18.60 native pixels**
away, but the significant-neighbor preflight records **zero
significant** neighbors. The 18.6-pixel candidate is neither
demonstrated to contaminate the star nor demonstrated to be harmless
under the current flux-threshold proxy. This makes a future
star+neighbor+sky forward model particularly appropriate.
All science-adoption flags remain false, including extended wings,
spatial representativity, stellar contamination modeling, and
coadd covariance. Do not repeat unchanged g-only PSF builds to tune
a threshold against these same three stars.


### P14D — original-coadd joint star + sky + point-neighbor pilot

**Implementation:** \`src/lsbpipe/photometry/psf_joint_scene.py\`,
connected to \`build_empirical_coadd_psf\` as
\`diagnostic_metrics.fixed_psf_joint_native_scene_qa\` and to the existing
Photometry PSF quality panel. This stage **does not refit the ePSF**.
The normal empirical ePSF builder and train/holdout split finish first,
and its *frozen nonnegative native kernel* is then fitted to each of
the same previously selected training stars and reserved Gaia holdouts.
No feedback enters the PSF builder, sky subtraction, source selection,
kernel clipping, global PSF provenance or science-adoption gates.

#### Forward model and measurement geometry

On original, native, un-smoothed and un-subtracted DR11 image pixels,
in a larger square with side \`2*kernel_width - 1\`, evaluate

\`\`\`
I(x,y) = A_star * P(x - x_star, y - y_star)
       + sum_j A_j * P(x - x_j, y - y_j)
       + b0 + bx*(x-x_star) + by*(y-y_star) + epsilon(x,y).
\`\`\`

Here \`P\` is the *already measured* experimental ePSF kernel, not
the Tractor per-exposure optical PSF, and \`A_star,A_j>=0\` are
conditional nonnegative nuisance/source flux amplitudes. The full
three-coefficient planar background is unconstrained and is fitted
**simultaneously**, not subtracted beforehand. Positions come from
the original same-brick Tractor centroid projection for catalog
PSF neighbors; the primary source is uniquely matched to its saved
Gaia ID. Candidate point neighbors are searched over the whole
expanded field plus the existing ePSF support, not just the old
50-pixel/2%-of-primary significant-neighbor gate.

For g with the existing 81×81 ePSF, the diagnostic region is 161×161
native pixels. The factor two is a **declared exploratory measurement
geometry**, *not* a physically derived radius where the sky is free
of PSF wings. The one-coordinate native source model is cubic
interpolated at subpixel positions with SciPy
\`ndimage.map_coordinates\`, order 3, constant-zero exterior
and nonnegative basis values (the original fitted kernel is not
modified). Source amplitudes are fitted by nonnegative least
squares **after projecting the three free sky parameters out via
QR**. Native coadd invvar weights are used only for relative
weighted residual comparison. These weights do **not** model the
correlated coadd noise covariance or give calibrated parameter errors.

The same pixels are also fitted with **central star + plane only**,
yielding descriptive residual sums with and without Tractor point
neighbors. Since the full model is nested and has more freedom,
the reduction in squared residuals is **not** evidence significance,
a p-value, a Bayes factor, or proof that its amplitudes are accurate.
Reports contain individually fitted point-neighbor fluxes, model
neighbor/central flux ratios in the 1× and 4× guessed-FWHM native
apertures, the joint sky relative to the old 30–38-pixel sky estimate,
the full vs null weighted squared residuals, and a matrix condition
number. The original input quartet and kernel hashes are preserved.

#### Important unresolved astrophysical effects

- Because \`P\` has finite 81×81 support, the method **cannot**
  reconstruct true stellar wings outside that radius. Wing light
  beyond it can still be absorbed by \`b0,bx,by\`.
- Tractor's PSF classification does not prove a stellar point source;
  catalog extended sources are counted as **unmodelled**, rather than
  silently forced into a PSF shape. The nearest unmodelled catalog
  distance is retained. Their light can still contaminate the fit.
- The nested full/null residual comparison has additional fitted
  degrees of freedom. Neither relative coadd invvar nor source count
  yields a statistically valid significance or independent PSF
  validation without covariance and catalog incompleteness analysis.
- The all-PSF-neighbor source cap of 18 is a **compute budget**.
  If more PSF neighbors overlap a scene, the fitter rejects that
  diagnostic instead of silently discarding weaker neighbors.
  Matrix-rank and degrees-of-freedom checks reject numerically
  nonidentifiable fits, and the original core pixel must be valid.
  The scene fit is NOT allowed to create or inpaint pixels.
- Some previously accepted stars will be too close to an image edge
  for the expanded 161px region. They receive an explicit, local
  non-success status and remain unchanged in the PSF training or
  reserved holdout. Failed scene fits cannot be dropped from the
  reported denominator or imply their acceptance by default.

The overall PSF status and all four science-validation flags remain
**NOT_ADOPTED / FALSE**. The actual next scientific work is to allow a
data-driven extended PSF wing component, test sky/wing
identifiability with independent blank-sky pixels, infer
coadd correlation and uncertainty, and validate against new
brightness/spatially separated heldout stars plus galaxy
injection-recovery, **without tuning against the existing three g
holdouts**. That scientifically calibrated stage is not implemented
by this exploratory decomposition.

Implementation references:
- SciPy \`optimize.nnls\`: nonnegative least-squares solution:
  https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.nnls.html
- SciPy \`ndimage.map_coordinates\`: cubic subpixel interpolation:
  https://docs.scipy.org/doc/scipy/reference/generated/scipy.ndimage.map_coordinates.html
- Legacy Survey coadds are inverse-variance-weighted, Lanczos-resampled
  images in nanomaggies/pixel, and coadd inverse variance is not an
  independent-pixel covariance matrix:
  https://www.legacysurvey.org/dr11/


### P14E — first P14D original FITS run and P14D numerical repair

User-provided full Leo P g joint-scene report
(`Вставленный текст(3).txt`; **2,217,091 bytes**, not committed)
confirms input identity `dr11/south/1554p180/g`, unchanged
recipe and empirical nonnegative PSF native-kernel SHA256
`8d976b91b4760fc8c51f607ccaf1cc59d4cac622c4c9992e5df11044bed2852a`,
and the original 10+3 Gaia split. The PSF is still **NOT ADOPTED**.

The first v1 joint-native scene fit has a real source-support issue:
**12/13** fixed stars fitted numerically, **2/3** Gaia holdouts;
`star-0110` stopped at
`SOURCE_TEMPLATE_OUTSIDE_USABLE_SCENE`. A Tractor centroid can
appear within the approximate square scene+kernel search rectangle
yet have **identically zero support** of the finite shifted
81×81 PSF on valid 161×161 native pixels. A zero column must not
make the central star unfit. P14E now excludes **only exact-zero**
basis columns, with ID, location, count and explicit
`EXACT_ZERO_FROZEN_KERNEL_ON_ALL_VALID_SCENE_PIXELS` evidence.
Any nonzero basis column stays in the simultaneous fit, subject to
the existing honest rank/DOF tests; zero finite-kernel overlap is
**not** evidence of absent unknown wide-angle wings. This should
recover the numerical path for star-0110 if no other source or
mask failure occurs; original FITS verification is still required.

The original v1 **full-scene** weighted RSS improvements were:
`star-0032` **92.6353%**, `star-0036` **82.9260%**,
`star-0050` **3.0731%**, and **9/12** other completed
scenes under **1%** (median 0.4524%). The dramatic improvements
occur with **bright, distant** catalog PSF neighbors: for
`star-0032`, Tractor objid **1887** at **93.9036 px**
with catalog fitted g flux **948.46 nMgy**; for
`star-0036`, objid **177** at **101.265 px**, catalog flux
**16.78 nMgy**. Such neighbors can dominate the objective
function of the **161×161 scene** without explaining
central-star structure in 1–4 FWHM. In fact, the v1 model's
catalogued PSF-neighbor/central-model cumulative-flux ratio
inside 4×guessed FWHM is **0** for star-0032 and **0.00556**
for star-0036. Those conditional ratios omit any unmodelled
catalog extended/other components and unknown wide wings.

Every one of the 12 numerically completed v1 scenes has **5–20
unmodelled catalog objects**, hence none is scientifically
deblended. `star-0032` has **8** and its nearest one is
**18.599 px** away; its TYPE and flux were not serialized by
the original v1 summary, and must not be guessed. The fitted
joint-minus-original-annulus sky for that star is
**−0.000193145 nMgy/native px**, or about −0.061
original per-pixel local RMS (not sigma significance).
`star-0148` sky difference is −0.000109681 nMgy/native px.
The overlarge global residual change does **not** validate the
original PSF, cure earlier holdout growth excess, or establish
catalog neighbour completeness.

P14E adds fixed-aperture, **same native pixel set**
weighted-RSS comparisons at 1× and 4× guessed FWHM,
re-using each full-scene/null model's **globally fitted**
parameters with **no new local optimization**. Importantly,
the signed local fractional change is **not clamped**:
the fitted neighbor model may improve the total field while
**worsening** the central-star residuals. It adds exact-zero
template accounting, the previously unexposed nearest unmodelled
Tractor source ID/TYPE/catalog band flux, and compact GUI
local-vs-global summaries. Schema now
`desidera.psf0-fixed-psf-joint-native-scene/2`,
implementation fingerprint incremented. These are descriptive
diagnostics on preselected stars, not hyperparameter tuning or
noise-covariance-calibrated quality gates.

A new original-FITS g run is **needed once** to verify that
star-0110 no longer fails due solely to an all-zero nuisance
template, reveal TYPE of the 18.6-pixel unmodelled neighbor at
star-0032, and evaluate true **central-aperture** differences
at 1×/4× FWHM. Do not infer an improvement before that run.


### P14F — observed P14E Leo P g closure and noncausal RSS partition

The *second original-FITS* g run was received (P14E v2, separate
original user report, not committed). Dataset
`dr11/south/1554p180/g`; unchanged 81×81 experimental kernel SHA256
`8d976b91b4760fc8c51f607ccaf1cc59d4cac622c4c9992e5df11044bed2852a`;
unchanged 10 training/3 reserved Gaia holdouts. Joint fixed-PSF
fits **13/13 numerical successes** and **3/3 holdouts**.
Exactly **one** catalog neighbor has zero finite-kernel support
(`star-0110`, `OBJID=2260`, separation 137.7734px), and is
explicitly excluded from the corresponding NNLS matrix with evidence.
No zero-support issue remains in this field's joint scene diagnostic.

**All 13 numerically fitted scenes have 5–20 unmodelled catalog
sources.** In particular `star-0032`'s nearest is
**Tractor `TYPE=DEV`, `OBJID=1941`, separation 18.5990px,
catalog g flux 0.367224 nMgy**. TYPE is a model classification;
it does not prove the astrophysical object class or flux accurately,
and this source has NOT been forward-modelled in P14D/E/F.
The pre-existing core ePSF remains `NOT_ADOPTED` with wide wings,
spatial representation, catalog source contamination, and correlated
coadd covariance unvalidated.

For `star-0032`, P14E v2 obtains weighted global RSS reduction
**92.6353%**, yet same-native-pixel central-aperture reduction
**4.8568%** (1 guessed FWHM) and **8.1843%** (4 guessed FWHM).
Crucially, **all fitted catalog point neighbors contribute an
EXACT ZERO frozen-PSF flux inside the 4×FWHM aperture**, despite
the local 8.18% residual improvement: those point sources are
farther away than the finite PSF support permits.
The local difference MUST therefore result from the refitted
central-star amplitude and/or simultaneous sky plane conditioned
on the bright distant sources; it is not direct deblending of the
18.6px `TYPE=DEV` object. For `star-0036`, a global
**82.9260%** reduction accompanies local **−1.5843%**
(1×FWHM) and **−0.3259%** (4×FWHM) *worsening*. The
remaining holdouts: `star-0110` global +0.1813% versus
4×FWHM **−0.1329%**, `star-0148` global +0.0582% versus
4×FWHM **+0.0102%**. These effects are *conditional,
not calibrated significance measures*.

To prevent misinterpreting conditioning as astrophysical causal
attribution, schema v3 reports on the SAME original native
pixels and SAME frozen globally fitted parameters the exact
algebraic model difference

```
delta_model = (A_full - A_null) * central_P
            + sky_plane(full - null)
            + sum_j(A_j * neighbor_P_j).
```

For each central region (1× or 4× guessed FWHM) and the entire
scene, with inverse-variance weighted null residual vector `e0`
and weighted component difference vectors `d_i`, define

```
RSS_null - RSS_full = 2 <e0, sum_i d_i> - ||sum_i d_i||^2,
gain_i = 2 <e0, d_i> - <d_i, sum_j d_j>,
sum_i gain_i = RSS_null - RSS_full.
```

This partitions the pairwise cross terms symmetrically and therefore
closes algebraically to rounding accuracy. The three explicitly
reported components are **central amplitude reallocation**,
**planar-sky reallocation**, and **direct catalog-point neighbors**.
Also output direct-neighbor model weighted power (strict zero means
zero predicted neighbor contribution on those pixels). A gain
allocation does **NOT** distinguish independent physical causes:
the components and their fitted amplitudes are globally coupled
and can absorb unknown wide stellar wings or unmodelled catalog
galaxies. Negative component gains are retained, there is no
additional fit, no new thresholds, no star resplit, no modification
to the measured PSF or its NOT_ADOPTED state.

The P14E report already supplies the qualitative conclusion of
the experiment. One new original g run with schema v3 is useful
ONLY to determine the relative mathematical shares of the
sky plane and central-star amplitude in `star-0032`'s
local 8.18% improvement and check cross-term closure.
It cannot measure the 18.6px DEV object's true profile,
the PSF wings outside the 81px support, or noise covariance;
those require new physically motivated forward models and
independent validation.


### P14F original-FITS closure — diagnostic stage complete

The third Leo P g original-FITS export (2,287,254-byte report, supplied by
the user and **not committed**) has scene schema
`desidera.psf0-fixed-psf-joint-native-scene/3`, dataset
`dr11/south/1554p180/g`, and the unchanged frozen PSF-kernel
SHA256 `8d976b91b4760fc8c51f607ccaf1cc59d4cac622c4c9992e5df11044bed2852a`.
The original 10 training + 3 fixed Gaia holdouts are still selected
without resplitting. **13/13** numerical native-pixel joint fits and
**3/3** fixed holdouts complete; **13/13** scenes still contain unmodelled
catalog source profiles. No galaxy or PSF science product is adopted.

For each fitted scene the three signed, symmetrically allocated
component RSS gains close to the original same-pixel RSS change
to floating-point precision. Across all 13 stars and all three
apertures (entire scene, 1 and 4 guessed FWHM) the maximum absolute
closure residual is **2.33e-10** in weighted-RSS units. This
verifies the implementation's **algebra**, not its physical adequacy
or measurement uncertainties.

Observed holdout results, signed percentages relative to the
same-aperture star+plane-only weighted RSS:

| Star | Full-scene gain | 4-FWHM gain | 4-FWHM sky allocation | 4-FWHM central-star allocation | 4-FWHM catalog-PSF allocation |
| --- | ---: | ---: | ---: | ---: | ---: |
| star-0110 | +0.1813% | -0.1329% | -0.1333% | ~0.0000% | +0.0004% |
| star-0032 | +92.6353% | +8.1843% | +8.1790% | +0.0053% | 0.0000% |
| star-0148 | +0.0582% | +0.0102% | +0.0103% | ~0.0000% | -0.0002% |

For **star-0032** the weighted RSS gain partition in 4 guessed
FWHM is `sky=587.146986`, `central=0.380418`,
`point_neighbors=0`, a 99.935%/0.065% split of the **positive
fitted RSS gain**, not a physical separation of photon sources.
The full-minus-null fitted sky at the star is
**-0.001244485 nMgy per native pixel** (full sky
**-0.000216988** versus null sky **+0.001027497**);
the nearest **unmodelled** Tractor source is
`TYPE=DEV, OBJID=1941, g catalog flux=0.367224 nMgy,
separation=18.5990 pixels`. The original stellar-annulus
background was **-0.0000238432 nMgy per pixel**, yet neither
fitted background is independently certified correct.
Because the finite-support point-source templates contribute
exactly zero within the central 4-FWHM region, the 8.1843%
local RSS gain is entirely due to globally coupled changes in
the sky plane and central-star amplitude under this
**incomplete** source model, not direct subtraction of a
nearby catalog PSF star. This does not measure the light from
the DEV source, nor does the faint catalog-integrated flux
establish that it is harmless.

As a counterexample, **star-0036** has a full-scene
gain of **+82.9260%**, but **-0.3259%** degradation inside
4 FWHM; the algebraic 4-FWHM partitions are
`sky=-0.9738%`, `point neighbors=+0.6473%`,
`central star=+0.0006%`.
The original-FITS evidence therefore **rejects global
weighted RSS improvement as an empirical-PSF science gate**.

**Do not continue the P14D/E/F fit-only diagnostic loop.**
Its original observational question has been answered. New
scientific information needs a different forward model and
independent data tests:

1. Confirm native Tractor catalog availability, coordinate
   transforms, and completeness of each source's `type`,
   `shape_r`, `shape_e1`, `shape_e2`, flux, and mask
   information. Do not manufacture structural parameters
   when absent. `REX`, `EXP`, and `DEV` must use
   documented and type-correct, PSF-convolved *extended*
   templates; diagnostic masks are an acceptable control.
2. Separate the unresolved central empirical PSF core,
   physically calibrated wide-angle wings and local sky
   with an identifiability/sensitivity experiment on
   larger un-subtracted native scenes. Explicitly check
   boundary effects and bright distant sources, rather
   than assuming the 81-pixel empirical support equals
   the true wing boundary.
3. Compare predictive, fixed-split holdout residuals
   across models; calibrate coadd noise covariance
   before treating pixel-weighted RSS as likelihood-based
   evidence. Report injection-recovery bias and
   uncertainty for galaxy surface brightness, not
   residual gains alone.
4. Retain separate per-band g/r/i/z PSF solutions,
   original raw data, QA provenance, and
   `NOT_ADOPTED` until justified against external,
   predefined science tolerances.

These are design requirements, NOT P15 implementation
or a claim of completed scientific calibration.


### P15A/P15B — native DR11 Tractor extended-neighbor pilot (2026-11-07)

Status: **implemented as conditional QA; original FITS P15 execution pending**.
This is deliberately distinct from the scientifically incomplete
P14D/E/F fixed-point-source joint scene and does not close the PSF
science-adoption gate. Current P14 ePSF SHA and original training/holdout
selection remain frozen. Source additions are confined to the existing
\`photometry\` and \`tests\` directories; existing PR branch retained.

**Catalogue provenance.** \`read_native_tractor_gaia\` now reads
\`SHAPE_R\` (arcsec), \`SHAPE_E1\` and \`SHAPE_E2\` (dimensionless) and
\`SERSIC\` (when present) from the *same local, hashed, original DR11
Tractor FITS* and retains their per-row values aligned with the native
WCS-projected centroids. Missing fields remain \`None\` — neither
source radius nor ellipticity is invented. This reader extension
does not change the conservative Gaia/Tractor PSF identity gate.

**Type-correct exploratory source geometry.**
\`src/lsbpipe/photometry/psf_extended_scene.py\` admits:

- \`REX\`: intrinsically **round** Sersic \(n=1\) (no inferred PA);
- \`EXP\`: Sersic \(n=1\) with catalogue ellipticity;
- \`DEV\`: Sersic \(n=4\) with catalogue ellipticity.

\`SER\`, \`DUP\`, unknown types, and any source lacking a valid
positive \`SHAPE_R\` or allowed \`SHAPE_E1/E2\` are explicitly
reported as **not modelled**. For the supported elliptical profiles,
\`epsilon=sqrt(e1^2+e2^2)\`,
\`b/a=(1-epsilon)/(1+epsilon)\`,
\`PA_east_of_north=(180 - atan2(e2,e1)*90/pi) % 180\`.
The local *original-image WCS* supplies east/north arcsec per
pixel, so image rotation and RA axis handedness are respected
rather than silently treating Tractor ellipses as pixel ellipses.

We render finite native-coordinate analytic Sersic \(n=1/4\)
profiles using deterministic 3×3 subpixel sampling and FFT
convolution with the **frozen nonnegative empirical PSF**.
This is a **morphologically plausible pilot**, **NOT a
pixelwise faithful reimplementation of Tractor**: Tractor uses
mixture-of-Gaussians profiles, its own WCS/pixel approximation,
and different effective PSF treatment. Our finite rendered
domain can truncate significant \`DEV\` wings and unmeasured
empirical stellar-PSF wings; fitted amplitudes are conditional
nuisance parameters, NOT calibrated catalogue total fluxes.
DR11 documentation: https://www.legacysurvey.org/dr11/catalogs/
and https://www.legacysurvey.org/dr9/description/ .

**QA-only nested native-scene comparison.** For the SAME
pre-selected star, original image/invvar/validity and original
161×161-pixel footprint, solve two independent constrained
models: (A) central frozen ePSF + catalog PSF point neighbors +
unconstrained planar sky; (B) the same plus all supported,
shape-complete, PSF-convolved \`REX/EXP/DEV\` sources. Fit
nonnegative source amplitudes with the existing projected NNLS
solver. Record original-source IDs and per-source parsing
status, count all omitted/nonrenderable profiles, matrix rank
failure, fitted sky difference, and **signed** central 1×
and 4× guessed-FWHM weighted RSS changes with the original
common pixel masks and globally fitted coefficients.
Comparison B has more free parameters, so its global RSS
improvement is **not** PSF accuracy or significance.
Source budget max=28 is an explicit computational cap;
budget overflow rejects the **whole** comparison rather than
choosing conveniently brighter neighbors.

P15 status is always \`PARTIAL_APPROXIMATE_EXTENDED_SCENE_DIAGNOSTIC\`
for numerical successes, or an explicit non-success reason.
The word *partial* is intentional even when all catalogued
extended candidates have usable shapes, because faint unlisted
sources, missing true profile wings, non-Gaussian noise and
other systematics remain. Original PSF, sky model, catalog,
star selections, train/holdout roles, original FITS, science
flags and patient-to-next-stage provenance remain unchanged.
A synthetic PSF-convolved mock with known extended-source
parameters demonstrates algebraic recovery under the
**identical rendering assumptions**; this is not empirical
evidence that DR11 Tractor profiles are reproduced to
scientific photometric tolerances.

**Remaining scientific Gate P15:** establish expected
one-pixel profile accuracy against native Tractor or equivalent
trusted reference; quantify native coadd covariance and
PSF-kernel uncertainty; measure true stellar wings on
larger, independently sampled sky; run prospective spatial
and brightness-matched holdouts and galaxy injection-recovery
with fixed, externally justified surface-brightness bias
tolerances. Until then g/r/i/z are independently
\`NOT_ADOPTED\`.


### P15C — predeclared 2–4 guessed-FWHM withheld-annulus prediction

The first P15A/B comparison is nested and assesses **in-sample**
weighted residual reduction. A model with more nuisance sources is
guaranteed not to increase whole-field RSS. The P15C improvement
introduces an independent **pixel-subset prediction diagnostic**,
not an independent PSF/population validation.

Before fitting either model, mark every *valid original* native
pixel at angular radius \`2 < r/FWHM_guess <= 4\` from each
preselected star as reserved. Fit the baseline (star + catalog PSF
point neighbors + free planar sky) and extended-source model
(star + PSF points + supported REX/EXP/DEV + the same free planar sky)
to exactly the same **complementary** pixels, without letting the
reserved ring influence any source amplitude or sky coefficient.
Then compare both predictions against the SAME entirely reserved
ring pixels using the original native inverse-variance weights.

The ring boundary is a **predeclared exploratory geometry**, not
the measured ePSF FWHM or a claimed physical wing cutoff. This
check is executed on the existing catalogue-derived, immutable
PSF templates and unchanged Gaia/Tractor train/holdout star split.
Its \`signed_predictive_ring_rss_improvement_fraction\` can be
negative (extended model predicts the held-out pixels worse).
The number of training/held-out valid native pixels, any rank
failure or absent usable ring, and both held-out RSS values are
serialized in
\`predeclared_2_to_4_fwhm_ring_prediction\`.
An insufficient or nonidentifiable split is a concrete non-success
status, never zero residual or a false PSF acceptance.

Neither the pixels nor the noise are statistically independent:
resampled DR11 coadds have correlated neighbouring pixel values,
and the frozen empirical PSF itself was trained from the same
star population. Morphologies and source lists are held fixed,
but they originate from the survey's source fitting. Consequently,
even a smaller **held-out pixel RSS** is not a calibrated
p-value, an independent holdout star, a PSF wing measurement,
or a scientific quality gate.

The existing GUI now separates this **out-of-fit** ring score
from the in-fit 1/4 FWHM and global RSS scores.
Synthetics cover an exactly known injected DEV profile,
a fully masked reserved ring (fail closed), and deterministic
reproduction with identical inputs. The P15 method fingerprint
records this explicit QA change without changing the fitted
empirical PSF kernel or any science-adoption flag.

No new run on original Leo P FITS is claimed at this stage.
The next observational test can be performed once the user's
local original data are available; existing Github CI tests
operate on synthetic native images and GUI fixture data.


**P15C synthetic stress cases (same original test package):**
Besides exact-profile injection/recovery, a deliberately incorrect
catalog DEV radius and ellipticity must produce worse reserved-ring
prediction than the correct fixed-injection morphology, under identical
pixels, mask, noise weights, frozen PSF and train/test boundary.
This tests sensitivity to source-model misspecification rather than
claiming real DR11 morphology precision. The full candidate-source
budget is now also checked *before* expensive convolution of all
shape-valid nearby objects: an overloaded scene receives an
explicit \`POTENTIAL_EXTENDED_SOURCE_BUDGET_EXCEEDED_BEFORE_RENDER\`
status, not a partially selected subset of conveniently fitted
galaxies. These checks are engineering and falsification evidence;
no statistical significance or science threshold is derived.


### P15D — predeclared Tractor morphology stress; **no catalog error model**

P15D is implemented inside the already conditional
\`p15_extended_tractor_scene\` result as
\`p15d_morphology_controlled_stress\`. No original Leo P FITS
run is claimed during this code-only stage.

**Why:** A nominal REX/EXP/DEV source shape in the native Tractor
catalog is itself an estimated quantity. The P15C reserve-annulus
fit tests model predictions conditional on that exact shape, but
does not say how sensitive the star/sky decomposition is to modest
structural misspecification. P15D provides a reproducible one-factor-
at-a-time (OAT) stress experiment without asserting that DR11
catalog errors have the selected magnitudes.

One catalogued extended source per preselected star is chosen
**deterministically by minimum native-pixel centroid distance**,
then Tractor \`OBJID\` for ties, among the profiles already
admitted in the same P15 scene. It is not chosen by the RSS
improvement, catalog brightness, or proximity to a heldout
star's residual feature. The other extended and point-source
profiles and the original frozen empirical PSF remain fixed.

The six **predeclared, two-sided engineering stresses** are:

| Varied quantity | Controlled changes | Units |
| --- | --- | --- |
| \`SHAPE_R\` | multiply by \`0.9\` and \`1.1\` | dimensionless fraction of *actual* DR11 half-light radius (arcsec) |
| \(\epsilon=\sqrt{e_1^2+e_2^2}\) | subtract/add \`0.05\` at fixed \(\mathrm{PA}\) | absolute dimensionless ellipticity, not \(b/a\) |
| position angle | subtract/add \`10°\` at fixed \(\epsilon\) | astronomical degrees east of north |

The original \`SHAPE_E1/SHAPE_E2\` doubled-angle components
are reconstructed exactly for the angle and ellipticity stresses:
\(\mathrm{PA}=(180^\circ-\frac12\operatorname{atan2}(e_2,e_1))\bmod180^\circ\).
An unphysical \(\epsilon<0\) or \(\epsilon\geq1\) is *rejected*,
never clipped to a valid profile. For the intrinsically circular
Tractor \`REX\`, only the two radius tests are meaningful;
\`REX\` ellipticity and PA variants are explicitly marked
\`NOT_A_REX_PROFILE_PARAMETER\`. A circular elliptical catalog
profile (\(\epsilon=0\)) has no defined PA; its PA/ellipticity
tests are refused rather than assigning an arbitrary axis.

All six trial records (including skipped/failed reasons) are
reported, never just the numerically preferred variations.
For each valid trial, the intrinsic Sérsic shape is re-rendered
and re-convolved with the same finite experimental PSF. The
existing native WCS, candidate identity, original pixel
intensities, invvar, validity and the geometrically reserved
\`2 < r/FWHM_guess \leq 4\` annulus are unchanged. One source
template column changes at a time; other catalog model columns
are unchanged. Source amplitudes and the free planar sky are
refitted using **only** the pixels outside the reserved ring.
Report the differences relative to the nominal catalog-shape
solution in:

- fitted central-star peak amplitude (nMgy/pixel);
- fitted sky level at the star (nMgy/native pixel);
- predictive weighted RSS on the **same** reserved annular
  pixels; negative apparent "improvements" remain visible.

The resulting minimum/maximum of those **six controlled responses**
is a descriptive *stress envelope*, **NOT** the error bar
of the galaxy flux, the uncertainty of \`SHAPE_R/e1/e2\`, a
confidence interval for the sky, or a statistical discovery
significance. The trial "radius fraction=10%; delta ellipticity
norm=0.05; delta PA=10°" inputs are neither DR11 fit
covariances nor universal calibration thresholds. Near-zero
nominal annular RSS can make fractional changes arbitrarily
large; absolute RSS differences are retained.

This is **not true uncertainty propagation**: a physical
catalog covariance model, source-to-source structural
correlations, PSF uncertainty, sky-wing degeneracy, spatial
noise covariance and survey model mismatch remain unresolved.
In particular the actual DR11 coadd pixels are correlated
across the reserved ring, and the empirical PSF was trained
on the same source field. The nearest rendered object is
not guaranteed to be the dominant contaminant; omitted
sources remain explicitly unmodelled in P15.

The regular QA panel prints the one-source stress counts and
sky/star-peak/ring-RSS envelope when meaningful. All P15D
quantities are QA-only: no changes to original FITS,
source selection, star train/holdout labels, PSF kernel,
its science-adoption state or downstream galaxy photometry.
The immediate validation is synthetic testing of exact
ellipticity/PA algebra, REX and circular-domain refusals,
physical boundary refusals, native-pixel injection response
and pre-fit candidate selection. A real Leo P g run can
follow once original FITS are accessible.


**Additional original Tractor inverse-variance provenance.**
The DR11 catalog also publishes optional diagonal formal
\`SHAPE_R_IVAR\` [arcsec\(^{-2}\)],
\`SHAPE_E1_IVAR\`, and \`SHAPE_E2_IVAR\`. The native same-brick
reader preserves these *unaltered*, if present, as
\`shape_r_ivar_arcsec_m2\`, \`shape_e1_ivar\` and
\`shape_e2_ivar\` and propagates them to the P15D selected-source
provenance in
\`nominal_catalog_formal_shape_ivars_not_covariance_or_stress_scale\`.
Their absence stays \`None\`; zeros or formally positive values
are recorded without forcing an error distribution. The source
does not provide the complete structural-parameter covariance
needed for fully joint uncertainty propagation, nor the
reconstruction error of our approximate finite PSF-convolved
Sersic profile; DO NOT conflate these raw inverse variances
with the deliberately fixed P15D stress magnitudes.

See the native official DR11 file column definitions:
https://www.legacysurvey.org/dr11/files/ and the official
Tractor catalog explanation:
https://www.legacysurvey.org/dr11/catalogs/ .


### P15E — separate analytic sampling convergence from external Tractor fidelity

**Implemented remotely in PR #43; not yet compared against an actual
external Tractor model image from DR11.** This is a necessary
distinction: the original P15A–D code analytically renders a finite,
pixel-sampled Sérsic n=1/n=4 profile and convolves it with the frozen
experimental *coadd* PSF. Tractor's official DR11 pipeline instead
uses a mixture-of-Gaussians (MoG) representation and a Fourier-space
convolution against a **pixel-convolved exposure PSF**. DR11 Tractor
fits sources on individual exposures, not against coadded pixels.
Consequently the hypothetical "same source" coadd P15 scene has
systematic *model and PSF lineage differences* even if its numerical
convolution converges.

Official external source specifications:
- DR11 algorithm and exposure/coadd distinction:
  https://www.legacysurvey.org/dr11/description/
- Tractor source, API and GPLv2/astrometry.net build requirements:
  https://github.com/dstndstn/tractor
- Tractor documented ExpGalaxy, DevGalaxy, EllipseE:
  https://thetractor.readthedocs.io/en/latest/intro2.html

The Tractor source is GPLv2 and may have optional compiled/native
dependencies. Desidera does not embed, reimplement from copied GPL
code, or transparently install it as a core dependency. No raw
Tractor reference stamp or exact same-PSF test has been supplied yet.
A test that uses the *same analytic Sérsic renderer* for injection and
recovery is not proof of agreement with Tractor, even when its
pixel-space residual is zero.

**P15E code-only numerical check.** For exactly the nearest
already-rendered extended neighbor (selected by geometric catalog
distance and OBJID *before* inspecting model residuals), use the same
source centroid, original native WCS, finite scene footprint,
valid-pixel mask, same-shape metadata, and frozen empirical PSF,
but render the analytic intrinsic Sérsic profile using deterministic
odd subpixel integration grids **3×3, 5×5 and 7×7**. The original
P15 fitted scene still uses 3×3; the 5/7 grids are read-only QA
figures and are never silently promoted to the photometric model.
On the same original valid pixels, compare each pair of models
(3→5, 5→7, 3→7) after *separately* renormalizing their finite-scene
flux to unity. The comparisons serialize L1, L2, maximum per-pixel
absolute deviations in common unit-scene-flux coordinates, and
the **relative finite-patch flux** change prior to normalization.
These are numerical integration/shape diagnostics, **not** total
source flux errors, not intrinsic wing recovery, and not a claimed
Tractor matching tolerance. A small 5→7 difference does not rule
out a common model bias or truncation.

**External-reference contract.** The separate
\`compare_analytic_stamp_to_external_tractor\` API can compare our
native stamp with a real, independently rendered reference image
when and only when it is actually supplied with nonblank
provenance: renderer \`dstndstn/tractor\`, version/commit, source
TYPE and identity, native WCS fingerprint, input PSF fingerprint,
and the exact rendering recipe. It rejects missing provenance,
mismatched arrays, negative or zero reference pixels and
unrecognised renderer names. It compares shapes on the **same
supplied mask**, renormalized to each finite patch's total
included flux. These labels are caller-provided metadata and are
not themselves verified cryptographically: even a numerical
pixel-perfect match is reported as
\`EXTERNAL_REFERENCE_ARRAYS_COMPARED_NOT_SCIENCE_CERTIFIED\`,
**not** Tractor equivalence. A true external comparison also
requires auditing WCS, PSF, pixel sampling and source-parameter
lineage and, ultimately, preserving the *exposure-vs-coadd*
distinction. Until then every original P15 scene explicitly reports
\`ORIGINAL_MATCHED_TRACTOR_REFERENCE_NOT_SUPPLIED\`, and the
PSF science gate remains closed.

This new evidence is serialized as
\`p15e_analytic_subpixel_convergence\` and
\`p15e_external_tractor_reference\` under the existing
\`p15_extended_tractor_scene\` object. The main PSF kernel,
star sample/split, original FITS, and downstream photometry
are unmodified, with \`science_adopted=false\`. Synthetic tests
are permitted to verify numerical consistency, masks, status
refusal and reference comparison arithmetic; they do NOT turn
a synthetic "Tractor-labelled" mock into a genuine external
Tractor result. Actual DR11-reference reconciliation is still
a research task for a future stage.
