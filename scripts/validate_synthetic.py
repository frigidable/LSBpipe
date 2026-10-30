"""Independent synthetic flux comparison across q and Sérsic index.

Run from repository root: PYTHONPATH=src python scripts/validate_synthetic.py
"""
import csv
from pathlib import Path
import numpy as np
from lsbpipe import ReferenceGeometry, MeasurementFrame, measure_profiles, SersicTruth, sersic_image, analytic_sersic_flux


def run():
    output = Path('validation_output')
    output.mkdir(exist_ok=True)
    results = []
    for q in [0.4, 0.7, 1.0]:
        for n in [0.7, 1.0, 2.0]:
            geo = ReferenceGeometry(95.15, 95.35, q, 36)
            truth = SersicTruth(geo, 19.0, n, 0.065)
            data = sersic_image((192, 192), truth, subpixels=8)
            prof = measure_profiles(MeasurementFrame(data, .262), geo, [0, 19, 38, 57, 76], subpixels=12)
            true_flux = analytic_sersic_flux(truth,76)
            recovered = float(prof.cog_raw_nMgy[-1])
            rel_error = (recovered - true_flux) / true_flux
            results.append(dict(q=q, n=n, true_flux=true_flux, measured_flux=recovered, relative_error=rel_error))
    with (output/'validation_matrix.csv').open('w', newline='', encoding='utf-8') as f:
        w=csv.DictWriter(f, fieldnames=results[0].keys());w.writeheader();w.writerows(results)
    maximum=max(abs(row['relative_error']) for row in results)
    print(f"9 synthetic configurations: maximum absolute relative flux error = {maximum:.5%}")
    if maximum > .015:
        raise SystemExit('Validation matrix exceeds 1.5% prototype integration tolerance')
    return results


if __name__ == '__main__':
    run()
