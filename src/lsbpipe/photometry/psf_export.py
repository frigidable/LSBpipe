"""Explicit diagnostic PSF export: atomic immutable derivative artifacts only."""
from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import tempfile

from .psf_empirical import PSF0Result


def export_psf0_diagnostic(result: PSF0Result, output_root: str | Path,
                          *, explicitly_requested: bool = False) -> Path:
    """Save an experimental native-coadd ePSF, never overwrite source data.

    No save for synthetic/unhashed inputs. The directory is atomically
    published with a FITS kernel and full JSON validation receipt. The result
    is NOT a scientifically adopted PSF, and not registered as an adopted run.
    """
    if explicitly_requested is not True:
        raise ValueError("Explicit researcher request required before writing diagnostics")
    if not isinstance(result, PSF0Result):
        raise TypeError("Expected PSF0Result")
    if result.lineage_id is None or not result.source_hashes:
        raise ValueError("A locally SHA-256-verified native FITS quartet is required")
    from astropy.io import fits
    root = Path(output_root).expanduser().absolute()
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise ValueError("Unsafe PSF diagnostic directory")
    # Check all existing ancestors even if the output folder is absent.
    # Otherwise a parent symlink could redirect the write outside the
    # explicitly selected galaxy workspace.
    for parent in (root, *root.parents):
        if parent.is_symlink():
            raise ValueError("Symlink path not allowed for diagnostic outputs")
    root.mkdir(parents=True, exist_ok=True)
    tag = "psf0-" + result.lineage_id[:24]
    final = root / tag
    if final.exists() or final.is_symlink():
        raise FileExistsError("Immutable PSF-0 diagnostic already exists; will not overwrite")
    tempdir = Path(tempfile.mkdtemp(prefix=".partial-psf0-", dir=root))
    try:
        kernel_path = tempdir / "empirical_psf.fits"
        header = fits.Header()
        header["BUNIT"] = ("1", "Normalized discrete effective PSF")
        header["SAMPLING"] = (1, "Native pixels per effective PSF sample")
        header["BAND"] = result.dataset_id.split("/")[-1]
        header["PSFSTAGE"] = "PSF0-DIAGNOSTIC"
        header["NTRAIN"] = len(result.training)
        header["NHOLDOUT"] = len(result.holdout)
        header["PSFVALID"] = False
        header.add_history("Experimental PSF, NOT adopted photometric science.")
        header.add_history("Sky and PSF wings and coadd covariance remain unvalidated.")
        fits.PrimaryHDU(data=result.kernel.copy(), header=header).writeto(
            kernel_path, overwrite=False, checksum=True)
        report = result.report()
        report["fits_kernel_sha256"] = sha256(kernel_path.read_bytes()).hexdigest()
        payload = json.dumps(report, sort_keys=True, indent=2,
                             ensure_ascii=False, allow_nan=False).encode("utf8")
        (tempdir / "psf0_report.json").write_bytes(payload)
        (tempdir / "README.txt").write_text(
            "EXPERIMENTAL_PSF_NOT_ADOPTED\n"
            "This is a diagnostic native-grid coadd PSF. Do not use for "
            "publishable galaxy photometry/SBF before independent QA.\n",
            encoding="utf8")
        # Atomic directory publication on the same filesystem: no partial
        # result may be mistaken for a completed photometry run.
        os.rename(tempdir, final)
    finally:
        if tempdir.exists():
            shutil.rmtree(tempdir)
    return final
