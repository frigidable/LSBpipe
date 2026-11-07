# Leo P — second real-data PSF-0 pilot: PSFSIZE-driven 28-star diagnostic audit

**Observed:** 2026-11-04, DR11 south brick 1554p180, g band. This review transcribes user-supplied screenshots of the 28 training cutout cards; raw native FITS and full per-star numeric arrays are not available for this review. **No stars are manually or automatically excluded by this document.** Original Tractor SHA256 `8e8e3f75af868f3ef7cf4c4c9d7ca28210cbca87ef27c98351a6ab237010562c`. Report lineage `39fa76d25abffee067ed5dcd472bf2e309aead0e348e764b27433be7f9ade2b5`. Star IDs are detection-recipe-dependent, NOT stable across runs; Gaia IDs should be used for longitudinal crossmatching.

## Measurements read from user evidence

- Native coadd `legacysurvey-1554p180-psfsize-g.fits.fz` was newly downloaded and successfully sampled: 5×5 robust median `PSFSIZE_g = 1.721 arcsec`; image WCS pixel scale `0.2620 arcsec/pix`, detection FWHM guess `6.57 pix`.
- `outer_sky_robust` experiment: 51-pixel cutouts, sky annulus 19–24 pixels, 2 possible training residual passes; 28 actual training stars, 8 held-out stars, 972 detected candidates.
- Signed negative kernel mass fraction `0.74%`; outer holdout median `|residual|/peak = 0.0126`; median holdout core `|residual|/peak = 0.0196`. These are not whitened/noise-calibrated statistics and do NOT validate science PSF.
- Current sky ring `19/6.57 = 2.89` to `24/6.57 = 3.65` **FWHM**. Relative to the previous guessed 4.80px case it lies substantially closer to the stellar core; wing absorption by planar sky fit is unresolved.
- Automatic train-star rejection was correctly **BLOCKED** by residual score–amplitude coupling (`rho = +0.940`); candidates `star-0002, star-0016, star-0022` were NOT excluded. One candidate was rejected via new `MASKED_STELLAR_PSF_CORE_PIXELS`, and two via width heuristic; 28/8 sample change cannot be attributed uniquely to these three alone, because detection and spatial inventory also changed.

## Every visible training card (transcribed)

Columns `core` and `outer` are **median absolute 2D cutout−model residual divided by that star's own measured peak**, not calibrated chi-square, not model-error-aware, and not mutually comparable across S/N. `radial/RMS` is sky-annulus inner minus outer median residual divided by *local* RMS; a radial trend can reflect stellar wings but does not establish causality.

| Star | Gaia G | Tractor S/N (diag only) | Core | Outer | Radial/RMS | Audit observation |
|---|---:|---:|---:|---:|---:|---|
| star-0014 | 16.43 | 1238.6 | 0.0118 | 0.0006 | +0.17 | Ровное ядро, почти нулевые внешние остатки; рабочий эталон |
| star-0009 | 16.64 | 1102.4 | 0.0166 | 0.0004 | +0.21 | Небольшой концентрический центральный остаток; проверить систематику ядра |
| star-0115 | 19.03 | 141.1 | 0.0183 | 0.0123 | -0.04 | Внешняя область шумовая; не использовать для определения широких крыльев |
| star-0040 | 17.13 | 610.4 | 0.0160 | 0.0019 | +0.11 | Хорошее ядро; gradient/RMS=0.51 требует проверки локального фона |
| star-0190 | 19.65 | 79.8 | 0.0354 | 0.0280 | -0.05 | Шум ограничивает силу вывода по крыльям |
| star-0085 | 19.17 | 198.2 | 0.0198 | 0.0096 | +0.05 | Диагональная маскированная структура вне ядра; проверить остаточные пиксели sky annulus |
| star-0052 | 18.85 | 348.4 | 0.0164 | 0.0033 | -0.08 | Профиль в целом согласуется; проверить субпиксельное положение |
| star-0079 | 18.69 | 235.4 | 0.0193 | 0.0073 | -0.11 | Радиальный профиль близок; умеренная шумовая компонента |
| star-0287 | 19.83 | 47.3 | 0.0479 | 0.0395 | +0.09 | Сильная относительная шумовая компонента; слабый wing-информатор |
| star-0043 | 18.17 | 566.3 | 0.0134 | 0.0018 | +0.12 | Визуально неплохое ядро, малая относительная внешняя ошибка |
| star-0022 | 16.72 | 1028.0 | 0.0410 | 0.0009 | +0.26 | Заметный отрицательный центральный остаток и положительное окружение |
| star-0032 | 17.15 | 790.3 | 0.0109 | 0.0013 | +0.19 | Низкая относительная core/outer невязка; пригодна для контрольного сравнения |
| star-0002 | 14.71 | 2888.6 | 0.0355 | 0.0002 | +0.98 | Очень яркая; концентрический отрицательный центр; sky=0.00848, gradient/RMS=0.91 |
| star-0016 | 16.64 | 1175.1 | 0.0487 | 0.0008 | +0.23 | Выраженный отрицательный центр и положительное окружение, как у star-0002 |
| star-0131 | 20.44 | 111.4 | 0.0516 | 0.0205 | +0.04 | Слабая звезда, центральная форма плохо согласуется; проверить локальную PSF |
| star-0071 | 19.22 | 254.5 | 0.0557 | 0.0071 | -0.05 | Высокая core/peak невязка и кольцевидный residual; проверить центр/пространственную PSF |
| star-0274 | 20.27 | 49.0 | 0.0358 | 0.0370 | +0.12 | Низкий S/N и очень шумные крылья |
| star-0340 | 20.32 | 41.6 | 0.0613 | 0.0458 | -0.19 | Наибольшая core+outer relative discrepancy; в первую очередь оценить шум |
| star-0142 | 20.68 | 107.1 | 0.0165 | 0.0154 | +0.07 | Ядро компактное; шумная периферия |
| star-0028 | 16.63 | 765.7 | 0.0140 | 0.0009 | +0.22 | Слабая центральная кольцевая структура; хороший кандидат для сравнения ярких звёзд |
| star-0050 | 17.62 | 438.4 | 0.0119 | 0.0030 | -0.10 | Низкая core/outer невязка; полезный контроль |
| star-0011 | 16.24 | 1351.7 | 0.0145 | 0.0004 | +0.07 | Компактный источник, малые относительные остатки |
| star-0007 | 15.85 | 1793.5 | 0.0117 | 0.0003 | +0.08 | Один из лучших по относительным остаткам ярких кандидатов |
| star-0148 | 20.96 | 105.4 | 0.0202 | 0.0173 | +0.16 | Очень слабая по Gaia G, внешний профиль близок к шумовому |
| star-0062 | 19.08 | 327.8 | 0.0103 | 0.0045 | +0.00 | Минимальный core/peak среди 28; полезный контроль умеренной яркости |
| star-0055 | 18.42 | 373.0 | 0.0115 | 0.0036 | -0.11 | Низкая core невязка; octant spread=0.19 и возможная пространственная неоднородность |
| star-0036 | 17.88 | 718.2 | 0.0113 | 0.0015 | -0.04 | Неплохой контрольный bright/mid-SNR кандидат |
| star-0025 | 17.33 | 860.6 | 0.0129 | 0.0010 | +0.08 | Согласованное ядро; низкая относительная внешняя ошибка |

## Quantitative analysis of transcribed per-star values

Recalculation of Spearman rank correlations from the 28 manually read card labels (not original numerical pixel data) gives **rho(outer median |R|/peak, Tractor S/N) = −0.993**, and **rho(outer, Gaia G) = +0.952**. Outer-error median for 15 stars with Gaia G≤18.5 is ~0.0009 versus ~0.028 for 7 stars with Gaia G≥19.5 (around ×31). This demonstrates strong amplitude/noise coupling in the *displayed* metric; it is not a universal empirical model of noise and should not be used for star rejection across luminosities. These numeric correlations are descriptive; S/N derives from fitted coadd catalog assuming diagonal errors and stars share spatial selection biases.

The three algorithm-proposed outliers `star-0002`, `star-0016`, `star-0022` exhibit a common red/negative central residual with cyan/positive surrounding structure in the **signed** difference images. A similar morphology can be seen for other stars (for example `star-0071`). This is consistent with *a common PSF core/centroid or nonlinear/saturation mismatch*; it is NOT enough to classify them independently as contaminated. `star-0002` also has unusually high annular radial difference `+0.98 RMS`, retained fraction ~100%, and low angular/octant scatter `0.17 RMS`, so smooth wing contamination is plausible, not established. Core-fitting amplitude and centering residuals, variance weights and signed model are needed to distinguish explanations.

Bright clean controls for comparison: `star-0007`, `star-0011`, `star-0014`, `star-0032`, `star-0025`. Weak/noise-limited outer-wing probes: `star-0190`, `star-0287`, `star-0274`, `star-0340`, `star-0148`; do NOT reject simply because outer/peak looks large.

A masked diagonal feature persists at `star-0085` outside the core; evaluate native MASKBITS/valid and the sky annulus separately. Previously analogous coordinate was called `star-0084`, emphasizing why stable Gaia IDs matter.

## Methodology recommendations in priority order

1. **Keep current ePSF non-adopted.** The 0.74% negative mass is far below experimental 2.5% gate but says nothing about wings and does not validate clipping of signed negative kernel.
2. **Stabilize the sample across tests.** With FWHM fixed at 6.57 px, run same-object, same-Gaia-ID comparisons across a sky-annulus parameter grid; log which objects disappear when the cutout/isolation footprint changes. Avoid interpreting cross-experiment residual differences as causal if the sample differs.
3. **Scale sky-annulus locations with seeing** and test multiple legal annuli, including ≥4.5 FWHM where cutout and isolation allow. 51-pixel cutout half-width=25 pixels cannot contain these larger annuli. Increasing cutout beyond 75 pixels may lose enough isolated stars to prohibit building a robust ePSF. This must be reported, never bypassed.
4. **Separate sky and wing components** in a model fit that admits signed flux, neighbors, sky-gradient uncertainty and a physically motivated wing profile; retain the native image/ivar/masks unchanged.
5. **Replace ranking by relative peak residual** with cross-fitted residuals in noise-aware units plus per-star fractional model error, stratified by S/N, spatial position, and color; account for correlated coadd noise instead of treating INVVAR as the full covariance.
6. **Spatial and subpixel validation.** Inspect quadrant/position trends, residual dipoles/rings, residual vs Gaia color, central pixel masking, potential saturation/nonlinearity and the finite `oversampling=1` kernel. Add explicit photutils convergence and subpixel phase diagnostics.
7. **Other bands.** User now has 30 local files including `psfsize-[griz]`; verify each band's own WCS/map/FWHM and build independent per-band ePSF before any color/photometric measurement. Do not use g PSF in r/i/z.

**Literature/checkable documentation:** https://www.legacysurvey.org/dr11/files/ ; https://www.legacysurvey.org/dr11/description/ ; https://docs.astropy.org/projects/photutils/en/stable/user_guide/epsf_building.html .
