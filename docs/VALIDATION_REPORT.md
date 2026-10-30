# LSBpipe v0.1 — Validation report

Date: 2026-10-30.

## Executed tests

- `PYTHONPATH=src python -m pytest -q`: **25 passed**.
- `PYTHONPATH=src python scripts/validate_synthetic.py`: **9 of 9** idealized models below pre-specified 1.5% integration-tolerance bound.
- `PYTHONPATH=src python -m lsbpipe demo --seed 42 --output demo_output`: completed, numerical and visual products created.

## Synthetic Sérsic numerical integration

Independent analytical truth is calculated from the incomplete gamma function,
using known intrinsic Sérsic parameters and a fixed reference ellipse. Source
pixels are formed by independent subpixel midpoint integration. Flux is
measured with an aperture-coverage subpixel integrator at higher sampling.

For q = 0.4, 0.7, 1.0 and n = 0.7, 1.0, 2.0 (9 combinations), with
Re = 19 pix and integration to 76 pix, the maximum absolute fractional
flux error was **0.00352%**. Full numeric results are in
`validation_output/validation_matrix.csv`. Note this is a **noise-free,
known-sky, mask-free, PSF-free** integration accuracy test. It says nothing
about real-sky photometric precision.

## Synthetic noisy and masked scene, fixed seed 42

| Quantity | Result |
|---|---:|
| Input intrinsic Sérsic Re | 20.000 pixels |
| Derived finite-aperture R50 (unmasked COG) | 19.932 pixels |
| Intrinsic analytic infinite Ftotal | 256.176 nanomaggies |
| Analytic F(<84 px) | 254.385 nanomaggies |
| Observed unmasked F(<84 px) | 255.619 nanomaggies |
| Observed valid-only masked F(<84 px) | 218.251 nanomaggies |
| Mean absolute fractional difference from analytic growth curve | 0.335% |

The R50 comparison is **illustrative**: finite-aperture half-flux and intrinsic
Re are not the same definition and the mock omits a PSF. The injected sky was
known and subtracted exactly. One noise realization does not quantify bias or
uncertainty calibration.

## Verified invariants

1. Constant field flux equals known intensity times *valid fractional area*.
2. A geometry rotation produces the expected major/minor-axis radii.
3. Elliptical weights are nested with growing semimajor axis.
4. Geometric ellipse areas agree with the analytic area at better than a 0.2%
   envelope in representative subpixel cases.
5. Cumulative raw flux equals the sum of disjoint annuli (same pixels/weights).
6. Inverse variance with zeros invalidates corresponding measurements.
7. Missing image footprint is not mistaken for observed valid area.
8. Fully masked annuli return missing intensity/annular flux, not measured zero.
9. Negative annular flux survives unchanged; AB surface-brightness magnitude
   is absent for nonpositive intensity.
10. No `maximum.accumulate` or otherwise enforced monotone COG is applied.
11. Input image and background arrays are copied and read-only within frame.
12. Nonmonotone raw COG does not silently produce a half-light radius.

## Not yet verified

- Photutils `exact` backend was not exercised in this environment (Photutils
  is not installed); only absence handling is tested. The interface is coded,
  but exact overlap requires a separate installation and validation.
- Pixel-to-sky conversion with reflected/rotated WCS; real Legacy FITS.
- Automatic residual-sky estimation, sky bias and correlated-noise intervals.
- PSF effects, mask reconstruction, free isophotes, astrophysical neighbors.
- Cross-platform Windows run and GPU/large-memory scaling.
- NGC2146sat regression: the supplied archives contain no science FITS.

Therefore **v0.1 is a mathematical proof-of-concept, not a production-quality
LSB photometry pipeline**.
