"""Interactive in-memory PSF training-star review for the existing GUI.

Cards show the exact local-sky-subtracted native cutout, the signed
candidate ePSF evaluated on the same pixel grid, and a *square* radial
diagnostic plot (observed vs model). A reviewer can exclude/restore only
training stars; independent holdouts are not mutable through this UI.

Images are diagnostic previews built from arrays already in worker memory.
No source FITS are modified, no manual decision is silently applied,
and no rejected model is adopted as a scientific PSF.
"""
from __future__ import annotations

import math
import numpy as np

from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import (
    QColor, QImage, QPainter, QPen, QPixmap,
)
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFrame, QGridLayout,
    QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)


IMAGE_SIDE = 112


def _square_image(values, *, relative_peak, valid=None):
    """Signed asinh contrast; equal angular/pixel scale for image and model."""
    data = np.asarray(values, dtype=float)
    if data.ndim != 2 or min(data.shape) < 9 or not np.isfinite(data).all():
        raise ValueError("Star review image requires a finite 2D cutout")
    peak = max(float(relative_peak), 1e-10)
    abs_value = np.clip(np.abs(data)/peak, 0, 100.)
    transfer = np.arcsinh(15. * abs_value) / math.asinh(15.)
    scaled = np.clip(np.rint(205*transfer), 0, 205).astype(np.int16)
    rgb = np.zeros((*data.shape, 3), dtype=np.uint8)
    rgb[:] = (30, 32, 38)
    pos = data >= 0
    neg = ~pos
    rgb[pos, 0] = 34
    rgb[pos, 1] = 50 + scaled[pos] * 3//4
    rgb[pos, 2] = 50 + scaled[pos]
    rgb[neg, 0] = 50 + scaled[neg]
    rgb[neg, 1] = 34
    rgb[neg, 2] = 40
    if valid is not None:
        mask = np.asarray(valid, dtype=bool)
        if mask.shape != data.shape:
            raise ValueError("Review mask and original cutout sizes disagree")
        rgb[~mask] = (95, 65, 12)
    ny, nx = data.shape
    return QPixmap.fromImage(
        QImage(rgb.tobytes(), nx, ny, nx*3, QImage.Format_RGB888).copy()
    ).scaled(IMAGE_SIDE, IMAGE_SIDE, Qt.KeepAspectRatio, Qt.FastTransformation)


def _radial_profile(values, center, *, peak):
    data = np.asarray(values, dtype=float)
    cy, cx = float(center[1]), float(center[0])
    y, x = np.indices(data.shape)
    rr = np.hypot(x-cx, y-cy)
    max_r = min(data.shape)//2.
    radii = np.arange(0., max_r, 1.)
    med = np.full(len(radii), np.nan)
    for i, r in enumerate(radii):
        sample = (rr >= r) & (rr < r+1) & np.isfinite(data)
        if np.any(sample):
            med[i] = np.median(data[sample])/max(float(peak), 1e-10)
    return radii, med


def _radial_plot(observed, modeled, center, *, peak):
    """Square QPainter profile; same cutout radius and both flux scales."""
    from PySide6.QtCore import QPointF
    image = QImage(IMAGE_SIDE, IMAGE_SIDE, QImage.Format_RGB32)
    image.fill(QColor(25, 28, 33))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing, True)
    left, right, top, bottom = 17., 5., 8., 16.
    axis_w = IMAGE_SIDE-left-right
    axis_h = IMAGE_SIDE-top-bottom
    def to_screen(x, y, xmax):
        # Signed flux normalized by measured star peak, not total PSF flux.
        return QPointF(
            left + (x/max(xmax, 1.))*axis_w,
            top + ((1.08 - np.clip(y, -.16, 1.08))/1.24)*axis_h)
    painter.setPen(QPen(QColor(95, 99, 106), 1))
    painter.drawLine(QPointF(left, top), QPointF(left, top+axis_h))
    painter.drawLine(QPointF(left, top+axis_h), QPointF(left+axis_w, top+axis_h))
    painter.setPen(QPen(QColor(85, 85, 85), 1, Qt.DashLine))
    y0 = to_screen(0., 0., 1).y()
    painter.drawLine(QPointF(left, y0), QPointF(left+axis_w, y0))
    series = (
        (observed, QColor(88, 210, 233)),
        (modeled, QColor(244, 155, 93)),
    )
    for values, color in series:
        xx, yy = _radial_profile(values, center, peak=peak)
        painter.setPen(QPen(color, 1.7))
        for index in range(1, len(xx)):
            if np.isfinite(yy[index-1]) and np.isfinite(yy[index]):
                painter.drawLine(
                    to_screen(xx[index-1], yy[index-1], xx[-1]+1),
                    to_screen(xx[index], yy[index], xx[-1]+1))
    painter.setPen(QColor(174, 178, 187))
    painter.drawText(QPointF(2, 12), "1")
    painter.drawText(QPointF(3, y0+3), "0")
    painter.drawText(QPointF(IMAGE_SIDE-25, IMAGE_SIDE-3), "r/pix")
    painter.end()
    return QPixmap.fromImage(image)


class PSFStarReviewDialog(QDialog):
    """All training cutouts, side-by-side native PSF and radial QA."""
    def __init__(self, snapshot: dict, *, parent=None):
        super().__init__(parent)
        if snapshot.get("schema") != "desidera.psf0-star-review-in-memory/1":
            raise ValueError("Unsupported or missing PSF training-star review data")
        self.snapshot = snapshot
        self._controls = {}
        self._min_train = int(snapshot.get("min_train_stars", 1))
        self.setWindowTitle("Desidera · PSF training-star mosaic · manual review")
        self.setObjectName("psf0_training_star_mosaic")
        self.resize(1160, 770)
        root = QVBoxLayout(self)
        heading = QLabel(
            "Training-star review · cyan positive, red negative, ochre masked. "
            "Each card: original sky-subtracted cutout / aligned candidate PSF / "
            "signed 2D residual / observed and model radial profiles "
            "(all square). Negative and positive residuals are equally visible.")
        heading.setWordWrap(True)
        root.addWidget(heading)
        notice = QLabel(
            "Exclusions affect ONLY the ePSF training sample. Independent "
            "holdout stars stay untouched. Changes apply only after clicking "
            "Apply & save exclusions + rebuild; current model remains non-adopted.")
        notice.setWordWrap(True)
        root.addWidget(notice)
        self.count_label = QLabel()
        self.count_label.setObjectName("psf0_review_counts")
        root.addWidget(self.count_label)
        scroll = QScrollArea()
        scroll.setObjectName("psf0_star_mosaic_scroll")
        scroll.setWidgetResizable(True)
        content = QWidget()
        grid = QGridLayout(content)
        grid.setContentsMargins(10, 10, 10, 10)
        grid.setHorizontalSpacing(9)
        grid.setVerticalSpacing(11)
        included = 0
        for index, star in enumerate(snapshot["stars"]):
            role = star["role"]
            if role not in ("train", "excluded", "auto_rejected"):
                continue  # holdout stars are never a selectable review control
            included += 1
            sid = star["star_id"]
            card = QFrame(content)
            card.setFrameShape(QFrame.StyledPanel)
            card.setObjectName("psf0_star_card_" + sid)
            box = QVBoxLayout(card)
            x, y = star["x_pix"], star["y_pix"]
            label = QLabel(f"{sid} · ({x:.1f}, {y:.1f}) pix")
            label.setToolTip(
                f"ICRS {star['ra_deg']:.7f}°, {star['dec_deg']:.7f}°"
                " · source pixels are not changed")
            box.addWidget(label)
            if role == "auto_rejected":
                auto_note = QLabel(
                    "AUTO-REJECTED from training by experimental "
                    "PSF-residual screening · view-only; restore by "
                    "choosing a non-robust experiment.")
                auto_note.setObjectName("psf0_auto_rejection_" + sid)
                auto_note.setWordWrap(True)
                box.addWidget(auto_note)
            gaia = star.get("gaia") or {}
            shape = star.get("shape_qa") or {}
            if gaia.get("gaia_source_id") is not None:
                gmag = gaia.get("gaia_g_mag")
                mag_text = f"G={gmag:.2f}" if gmag is not None else "G unknown"
                ratio = shape.get("axis_ratio")
                axis_text = (f"core axis ratio {ratio:.2f}"
                             if ratio is not None else "core shape unavailable")
                band_snr = gaia.get("tractor_band_snr_diagonal_only")
                snr_text = (f"Tractor S/N≈{band_snr:.1f} (diagonal only)"
                            if band_snr is not None else "S/N unknown")
                identity = QLabel(
                    f"Gaia DR3 {gaia['gaia_source_id']} · {mag_text} · "
                    f"{snr_text} · {axis_text} · separation "
                    f"{gaia.get('match_offset_pix', 0):.2f} px")
                identity.setWordWrap(True)
                identity.setToolTip(
                    "Gaia identity and Tractor PSF fit screened. These "
                    "are preliminary criteria, not clean-cutout certification.")
                identity.setObjectName("psf0_gaia_identity_" + sid)
                box.addWidget(identity)
            plots = QHBoxLayout()
            observed = np.asarray(star["cutout"], dtype=float)
            model = np.asarray(star["model"], dtype=float)
            if observed.shape != model.shape:
                raise ValueError("Star cutout and ePSF model grid differ")
            peak = star["peak_nMgy"]
            image_pixmaps = (
                ("Cutout", _square_image(
                    observed, relative_peak=peak, valid=star["valid"])),
                ("PSF model", _square_image(
                    model, relative_peak=peak, valid=star["valid"])),
                ("2D residual", _square_image(
                    observed-model, relative_peak=peak,
                    valid=star["valid"])),
                ("Radial PSF", _radial_plot(
                    observed, model, star["center_in_cutout"], peak=peak)),
            )
            for title, pixmap in image_pixmaps:
                panel = QVBoxLayout()
                legend = QLabel(title)
                legend.setAlignment(Qt.AlignHCenter)
                panel.addWidget(legend)
                graphic = QLabel()
                graphic.setObjectName("psf0_review_" + title.lower().replace(" ", "_") + "_" + sid)
                graphic.setPixmap(pixmap)
                graphic.setFixedSize(IMAGE_SIDE, IMAGE_SIDE)
                graphic.setToolTip(
                    "Original pixel grid, no interpolation of observed cutout. "
                    "PSF alignment/model only for review, not science-adopted.")
                panel.addWidget(graphic)
                plots.addLayout(panel)
            box.addLayout(plots)
            yy, xx = np.indices(observed.shape)
            xy = star["center_in_cutout"]
            rr = np.hypot(xx-float(xy[0]), yy-float(xy[1]))
            valid = np.asarray(star["valid"],dtype=bool)
            r_fwhm = float(snapshot.get("guessed_fwhm_pix", 4.8))
            core = valid & (rr <= r_fwhm)
            outer = valid & (rr > 2*r_fwhm)
            signed_resid = observed-model
            peak_norm = max(float(peak),1e-10)
            core_med = (float(np.median(np.abs(signed_resid[core])) /
                              peak_norm) if core.any() else None)
            outer_med = (float(np.median(np.abs(signed_resid[outer])) /
                               peak_norm) if outer.any() else None)
            residual_text = QLabel(
                "2D residual (same signed display scale) · "
                + (f"core median |Δ|/peak {core_med:.4f}" if
                   core_med is not None else "core unavailable")
                + " · "
                + (f"outer median |Δ|/peak {outer_med:.4f}" if
                   outer_med is not None else "outer unavailable")
                + " · diagnostic only, not covariance-calibrated χ²")
            residual_text.setObjectName("psf0_residual_stats_" + sid)
            residual_text.setWordWrap(True)
            box.addWidget(residual_text)
            if role == "auto_rejected":
                auto_check = QCheckBox("Automatically excluded (read-only)")
                auto_check.setChecked(True)
                auto_check.setEnabled(False)
                box.addWidget(auto_check)
            else:
                check = QCheckBox("Exclude from PSF training")
                check.setObjectName("psf0_exclude_" + sid)
                check.setChecked(
                    role == "excluded" or sid in set(
                        snapshot.get("excluded_training_star_ids", ())))
                check.toggled.connect(self._update_counts)
                self._controls[sid] = check
                box.addWidget(check)
            footer = QLabel(
                f"Sky {star['sky_nMgy']:.3g} nMgy/pix  · "
                f"gradient/RMS {star['gradient_sigma_span']:.2f}")
            footer.setWordWrap(True)
            box.addWidget(footer)
            sky = star.get("sky_qa") or {}
            if sky:
                metrics = QLabel(
                    f"Sky ring starts at {sky.get('annulus_inner_in_guessed_fwhm', 0):.2f} "
                    f"assumed FWHM · wing absorption risk: "
                    f"{'YES' if sky.get('wing_sky_degeneracy_warning') else 'still unverified'}"
                    " · fitted plane is NOT a joint PSF/sky estimate")
                metrics.setWordWrap(True)
                box.addWidget(metrics)
                # These *per-star* statistics are more informative than
                # repeating the same recipe-level wing warning 40 times.
                radial = sky.get("inner_minus_outer_residual_median_over_noise")
                sector = sky.get("octant_median_spread_over_noise")
                retained = sky.get("annulus_sigma_clip_retained_fraction")
                qa = QLabel(
                    "Sky annulus checks · "
                    + (f"inner−outer median/RMS {radial:+.2f}" if
                       radial is not None else "radial trend unavailable")
                    + " · "
                    + (f"octant spread/RMS {sector:.2f}" if
                       sector is not None else "octant spread unavailable")
                    + " · "
                    + (f"clipped annulus retained {100*retained:.1f}%" if
                       retained is not None else "retained fraction unknown")
                    + " · uncalibrated")
                qa.setObjectName("psf0_sky_stats_"+sid)
                qa.setWordWrap(True)
                box.addWidget(qa)
            grid.addWidget(card, index // 2, index % 2)
        if included < 1:
            raise ValueError("No original training stars available for review")
        scroll.setWidget(content)
        root.addWidget(scroll, stretch=1)
        footer = QHBoxLayout()
        reset = QPushButton("Restore all training stars")
        reset.setObjectName("psf0_restore_training")
        reset.clicked.connect(self._restore_all)
        footer.addWidget(reset)
        footer.addStretch(1)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)
        footer.addWidget(self.cancel_button)
        self.apply_button = QPushButton("Apply, save exclusions & rebuild")
        self.apply_button.setObjectName("psf0_apply_exclusions")
        self.apply_button.clicked.connect(self.accept)
        footer.addWidget(self.apply_button)
        root.addLayout(footer)
        self._update_counts()

    def _restore_all(self):
        for check in self._controls.values():
            check.setChecked(False)

    def _update_counts(self, *_):
        count = len(self._controls)
        excluded = len(self.excluded_ids())
        auto = len(self.snapshot.get("automatic_training_rejections", ()))
        remaining = count-excluded
        self.count_label.setText(
            f"Training candidates: {count} · excluded manually: "
            f"{excluded} · auto-excluded in this experiment: {auto} · "
            f"to rebuild: {remaining} (minimum {self._min_train}). "
            "Holdouts are never selectable.")
        self.apply_button.setEnabled(remaining >= self._min_train)

    def excluded_ids(self):
        return tuple(sorted(k for k, ctrl in self._controls.items()
                            if ctrl.isChecked()))

    def changed(self):
        return self.excluded_ids() != tuple(
            self.snapshot.get("excluded_training_star_ids", ()))


def ensemble_training_radial_plot(snapshot: dict, *,
                                  width: int = 540,
                                  height: int = 315) -> QPixmap | None:
    """Plot *real* per-star observed/model annular profiles and differences.

    Identical native-image annuli are applied to each star's local-sky
    subtracted cutout and its aligned, core-amplitude-fitted signed model.
    Only stars still marked 'train' are pooled; manually/automatically
    excluded stars and independent holdouts are NOT mixed in. The median
    is IN-SAMPLE, uncalibrated and must not be labelled as holdout QA.
    Returns None when there is no physically computed star/model pair.
    """
    if not snapshot or snapshot.get("schema") != (
            "desidera.psf0-star-review-in-memory/1"):
        return None
    profiles = []
    for star in snapshot.get("stars", ()):
        if star.get("role") != "train":
            continue
        observed = np.asarray(star.get("cutout"), dtype=float)
        model = np.asarray(star.get("model"), dtype=float)
        mask = np.asarray(star.get("valid"), dtype=bool)
        if (observed.ndim != 2 or model.shape != observed.shape
                or mask.shape != observed.shape or min(observed.shape) < 9):
            continue
        peak = float(star.get("peak_nMgy", 0))
        xy = np.asarray(star.get("center_in_cutout"), dtype=float)
        if (not np.isfinite(peak) or peak <= 0 or xy.shape != (2,)
                or not np.all(np.isfinite(xy))):
            continue
        rr = np.hypot(
            np.indices(observed.shape)[1] - xy[0],
            np.indices(observed.shape)[0] - xy[1])
        n_bins = max(0, min(observed.shape)//2)
        obs = np.full(n_bins, np.nan)
        pred = np.full(n_bins, np.nan)
        for radius in range(n_bins):
            annulus = (mask & (rr >= radius) & (rr < radius + 1)
                       & np.isfinite(observed) & np.isfinite(model))
            if annulus.any():
                obs[radius] = np.median(observed[annulus]) / peak
                pred[radius] = np.median(model[annulus]) / peak
        profiles.append((obs, pred))
    if not profiles:
        return None
    common = min(min(len(x), len(y)) for x, y in profiles)
    if common < 4:
        return None
    obs_matrix = np.array([x[:common] for x, _ in profiles])
    model_matrix = np.array([x[:common] for _, x in profiles])
    valid = np.isfinite(obs_matrix) & np.isfinite(model_matrix)
    obs = np.full(common, np.nan)
    pred = np.full(common, np.nan)
    diff = np.full(common, np.nan)
    for i in range(common):
        if valid[:, i].any():
            obs[i] = np.median(obs_matrix[valid[:, i], i])
            pred[i] = np.median(model_matrix[valid[:, i], i])
            diff[i] = np.median(
                obs_matrix[valid[:, i], i] - model_matrix[valid[:, i], i])

    image = QImage(width, height, QImage.Format_RGB32)
    image.fill(QColor(27, 31, 38))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing, True)
    left, right = 58., float(width)-16.
    top, top_end = 48., height*.63
    bot_start, bot_end = height*.73, float(height)-27.
    x_max = float(common - 1)

    def xx(i):
        return left + (right-left)*i/max(x_max, 1.)

    def yy_log(value):
        return top_end - (np.log10(value)+4.)/4.*(top_end-top)

    residual_scale = max(
        .025, float(np.nanpercentile(np.abs(diff[np.isfinite(diff)]), 95))
        if np.isfinite(diff).any() else .025)
    residual_scale = min(residual_scale*1.3, 2.)

    def yy_res(value):
        return (bot_start+bot_end)/2. - value/residual_scale*(
            bot_end-bot_start)/2.

    painter.setPen(QPen(QColor(73, 84, 97), 1))
    for exponent in range(-4, 1):
        y = yy_log(10.**exponent)
        painter.drawLine(QPointF(left, y), QPointF(right, y))
        painter.setPen(QPen(QColor(174, 187, 202), 1))
        painter.drawText(QPointF(6., y+3), f"1e{exponent}")
        painter.setPen(QPen(QColor(73, 84, 97), 1))
    painter.drawRect(QRectF(left, top, right-left, top_end-top))
    painter.drawRect(QRectF(
        left, bot_start, right-left, bot_end-bot_start))
    painter.setPen(QPen(QColor(143, 155, 168), 1, Qt.DashLine))
    painter.drawLine(QPointF(left, yy_res(0.)),
                     QPointF(right, yy_res(0.)))

    def draw_series(values, color, *, log=False):
        painter.setPen(QPen(QColor(*color), 1.9))
        last = None
        for i, value in enumerate(values):
            if not np.isfinite(value) or (log and value <= 0):
                last = None
                continue
            p = QPointF(xx(i), yy_log(value) if log else yy_res(value))
            if last is not None:
                painter.drawLine(last, p)
            last = p

    # Keep the data inside their own plot rectangles; low-S/N negative
    # flux cannot be rendered on the logarithmic top axis.
    painter.save()
    painter.setClipRect(QRectF(left, top, right-left, top_end-top))
    draw_series(obs, (136, 203, 240), log=True)
    draw_series(pred, (244, 182, 109), log=True)
    painter.restore()
    painter.save()
    painter.setClipRect(QRectF(
        left, bot_start, right-left, bot_end-bot_start))
    draw_series(diff, (140, 201, 232))
    painter.restore()

    painter.setPen(QColor(212, 222, 234))
    painter.drawText(QPointF(left, 15.),
                     f"Training-star radial medians  ·  N={len(profiles)}")
    painter.drawText(QPointF(left, 31.), "Log intensity / measured peak")
    painter.setPen(QPen(QColor(136, 203, 240), 2))
    painter.drawLine(QPointF(left+210, 27.), QPointF(left+224, 27.))
    painter.setPen(QColor(178, 195, 210))
    painter.drawText(QPointF(left+229, 31.), "observed")
    painter.setPen(QPen(QColor(244, 182, 109), 2))
    painter.drawLine(QPointF(left+308, 27.), QPointF(left+322, 27.))
    painter.setPen(QColor(178, 195, 210))
    painter.drawText(QPointF(left+327, 31.), "model")

    painter.setPen(QColor(178, 195, 210))
    painter.drawText(QPointF(left, bot_start-7.),
                     "Signed residual: observed − model / peak")
    # Residual scale was independently auto-selected; show its physical
    # display units rather than unlabeled plus/minus indicators.
    painter.drawText(QPointF(4., bot_start+7.),
                     f"+{residual_scale:.2g}")
    painter.drawText(QPointF(19., yy_res(0.)+4.), "0")
    painter.drawText(QPointF(4., bot_end),
                     f"−{residual_scale:.2g}")
    for radius in (0, int(x_max/2), int(x_max)):
        painter.drawText(QPointF(xx(radius)-5., bot_end+13.),
                         str(radius))
    painter.drawText(
        QPointF(right-123., height-5.), "Radius [native px]")
    painter.end()
    return QPixmap.fromImage(image)
