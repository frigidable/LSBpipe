"""Native PySide6 interactive viewer for *local original* Legacy FITS.

Astronomical FITS rows increase north/up; Qt images have a top-left
origin. This widget explicitly flips the display vertically and
undoes that flip when inspecting a pixel. World overlays use the
cutout WCS, not guessed pixel scales or a remote sky image.
"""
from __future__ import annotations

import numpy as np
from concurrent.futures import ThreadPoolExecutor
from PySide6.QtCore import QTimer

from .raster_cache import BoundedRasterCache
from .display_transfer import display_range, transfer_settings
from .quality_map import quality_classes

from PySide6.QtCore import Qt, Signal, QPointF
from PySide6.QtGui import (
    QImage, QPixmap, QColor, QPainter, QPainterPath, QPen,
)
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QCheckBox,
    QPushButton, QGraphicsView, QGraphicsScene, QGraphicsPixmapItem,
    QGraphicsPathItem, QSizePolicy, QLineEdit,
)

from .fits_raster import (
    rgb_for_layer, overlay_rgba, fits_pixel_from_scene,
)
from lsbpipe.survey import LEGACY_BITS, maskbits_to_uint


class FITSGraphicsView(QGraphicsView):
    pointHovered = Signal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        # Nearest-neighbor rendering is the safe default for mask/pixel QA.
        self.setRenderHints(QPainter.Antialiasing)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.setBackgroundBrush(QColor(22, 26, 34))
        self._relative_zoom = 1.

    def mouseMoveEvent(self, event):
        point = self.mapToScene(event.position().toPoint())
        self.pointHovered.emit(point.x(), point.y())
        super().mouseMoveEvent(event)

    def wheelEvent(self, event):
        factor = 1.22 if event.angleDelta().y() > 0 else (1 / 1.22)
        new_zoom = self._relative_zoom * factor
        if 0.20 <= new_zoom <= 24.:
            self._relative_zoom = new_zoom
            self.scale(factor, factor)
        event.accept()

    def fit_image(self):
        if self.scene() is None or self.scene().sceneRect().isEmpty():
            return
        self.resetTransform()
        self.fitInView(self.scene().sceneRect(), Qt.KeepAspectRatio)
        self._relative_zoom = 1.

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._relative_zoom == 1.:
            self.fit_image()


class FITSExplorer(QWidget):
    """Zoomable local FITS image with independent diagnostic overlays."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("fits_interactive_explorer")
        self._dataset = None
        self._overlays = []
        self._color_data = None
        self._manual_ranges = {}
        self._raster_cache = BoundedRasterCache(max_bytes=48 * 1024 * 1024)
        self._render_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="lsbpipe-fits-display")
        self._render_future = None
        self._render_active = None
        self._render_requested = None
        self._render_generation = 0
        self._displayed_base_key = None
        self._displayed_overlay_key = None
        self._render_timer = QTimer(self)
        self._render_timer.setInterval(30)
        self._render_timer.timeout.connect(self._poll_render)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(5)
        bar = QHBoxLayout()
        bar.setSpacing(8)

        bar.addWidget(QLabel("Layer"))
        self.layer = QComboBox()
        self.layer.setObjectName("fits_layer_selector")
        for name, kind in (
            ("IMAGE · nanomaggy", "image"),
            ("INVVAR · log weight", "invvar"),
            ("NEXP · contributing frames", "nexp"),
            ("MASKBITS · categories", "maskbits"),
            ("QUALITY · diagnostic", "quality"),
        ):
            self.layer.addItem(name, kind)
        bar.addWidget(self.layer, 2)
        bar.addWidget(QLabel("Stretch"))
        self.contrast = QComboBox()
        self.contrast.setObjectName("fits_contrast_selector")
        self.contrast.addItem("Standard", "standard")
        self.contrast.addItem("Faint features", "faint")
        bar.addWidget(self.contrast, 1)
        bar.addWidget(QLabel("Scale"))
        self.scaling = QComboBox()
        self.scaling.setObjectName("fits_scaling_selector")
        for name, key in (
                ("Auto", "auto"), ("Linear", "linear"),
                ("Asinh", "asinh"), ("Log10", "log")):
            self.scaling.addItem(name, key)
        bar.addWidget(self.scaling, 1)

        self.fit_button = QPushButton("Fit")
        self.fit_button.setToolTip("Fit the entire local FITS cutout into view")
        self.fit_button.clicked.connect(self._fit)
        bar.addWidget(self.fit_button)
        self.smooth = QCheckBox("Smooth preview")
        self.smooth.setObjectName("fits_smooth_preview")
        self.smooth.setToolTip(
            "Off: exact nearest-neighbor pixels; on: interpolated display "
            "only, not suitable for checking isolated mask pixels")
        bar.addWidget(self.smooth)
        layout.addLayout(bar)

        scale_row = QHBoxLayout()
        scale_row.addWidget(QLabel("vmin"))
        self.vmin = QLineEdit()
        self.vmin.setObjectName("fits_display_vmin")
        self.vmin.setPlaceholderText("auto")
        self.vmin.setToolTip("Lower display limit in native FITS units")
        scale_row.addWidget(self.vmin, 1)
        scale_row.addWidget(QLabel("vmax"))
        self.vmax = QLineEdit()
        self.vmax.setObjectName("fits_display_vmax")
        self.vmax.setPlaceholderText("auto")
        self.vmax.setToolTip("Upper display limit in native FITS units")
        scale_row.addWidget(self.vmax, 1)
        self.apply_limits = QPushButton("Apply range")
        self.apply_limits.setObjectName("fits_apply_display_range")
        self.apply_limits.clicked.connect(self._apply_display_range)
        scale_row.addWidget(self.apply_limits)
        self.auto_limits = QPushButton("Auto range")
        self.auto_limits.setObjectName("fits_reset_display_range")
        self.auto_limits.clicked.connect(self._auto_display_range)
        scale_row.addWidget(self.auto_limits)
        layout.addLayout(scale_row)
        self.range_info = QLabel("Display limits: automatic · native units")
        self.range_info.setObjectName("fits_display_range_info")
        self.range_info.setWordWrap(True)
        layout.addWidget(self.range_info)
        self.scale_bar = QLabel()
        self.scale_bar.setObjectName("fits_numeric_colorbar")
        self.scale_bar.setFixedHeight(13)
        self.scale_bar.setToolTip(
            "Color maps to the displayed native-unit limits above. "
            "This is not a calibrated photometric measurement.")
        layout.addWidget(self.scale_bar)

        toggles = QHBoxLayout()
        self.flags = {}
        for key, text, enabled in (
            ("SATUR", "SATUR", True),
            ("ALLMASK", "ALLMASK", True),
            ("BRIGHT", "Gaia BRIGHT", False),
            ("MEDIUM", "Gaia MEDIUM", True),
            ("GALAXY", "SGA GALAXY", False),
            ("INVALID", "No data / zero weight", True),
            ("OUTLINES", "Aperture outlines", True),
        ):
            check = QCheckBox(text)
            check.setObjectName("fits_overlay_" + key.lower())
            check.setChecked(enabled)
            check.setToolTip(
                "Display-only colored overlay; does not change the FITS "
                "pixels, mask policy or photometric acceptance")
            toggles.addWidget(check)
            self.flags[key] = check
        toggles.addStretch()
        layout.addLayout(toggles)
        self.color_legend = QLabel(
            "Red: SATUR  ·  Orange: ALLMASK  ·  Purple: BRIGHT  ·  "
            "Amber: MEDIUM  ·  Blue: GALAXY  ·  Magenta: no data. "
            "QUALITY: green valid, amber catalog warning, red/orange "
            "instrumental rejection, magenta numeric invalid. Display only.")
        self.color_legend.setObjectName("fits_overlay_legend")
        self.color_legend.setWordWrap(True)
        layout.addWidget(self.color_legend)

        self.context = QLabel(
            "Open an original FITS quartet from the Science QA panel.")
        self.context.setObjectName("fits_provenance")
        self.context.setWordWrap(True)
        layout.addWidget(self.context)
        self.render_status = QLabel("Pixel-accurate FITS display · no data loaded.")
        self.render_status.setObjectName("fits_render_status")
        self.render_status.setWordWrap(True)
        layout.addWidget(self.render_status)

        self.scene = QGraphicsScene(self)
        self.view = FITSGraphicsView()
        self.view.setObjectName("fits_graphics_view")
        self.view.setScene(self.scene)
        self.view.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self.view, 1)

        self.pixel_info = QLabel(
            "Hover over the image to inspect ICRS coordinates and raw FITS values.")
        self.pixel_info.setObjectName("fits_pixel_readout")
        self.pixel_info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.pixel_info.setWordWrap(True)
        layout.addWidget(self.pixel_info)

        self.provenance = QLabel(
            "LOCAL ORIGINAL FITS · DISPLAY ONLY · DIAGNOSTIC_NOT_ADOPTED")
        self.provenance.setObjectName("fits_science_status")
        self.provenance.setStyleSheet(
            "color: palette(mid); font-size: 11px;")
        layout.addWidget(self.provenance)
        self.base_item = QGraphicsPixmapItem()
        self.base_item.setZValue(0)
        self.overlay_item = QGraphicsPixmapItem()
        self.overlay_item.setZValue(1)
        self.base_item.setTransformationMode(Qt.FastTransformation)
        self.overlay_item.setTransformationMode(Qt.FastTransformation)
        self.scene.addItem(self.base_item)
        self.scene.addItem(self.overlay_item)

        self.layer.currentIndexChanged.connect(self._layer_changed)
        self.contrast.currentIndexChanged.connect(self._show_layer)
        self.scaling.currentIndexChanged.connect(self._scaling_changed)
        for key in ("SATUR", "ALLMASK", "BRIGHT", "MEDIUM", "GALAXY", "INVALID"):
            self.flags[key].toggled.connect(self._show_overlay)
        self.smooth.toggled.connect(self._set_smooth_preview)
        self.flags["OUTLINES"].toggled.connect(self._toggle_outlines)
        self.view.pointHovered.connect(self.inspect_pixel)

    def clear_data(self):
        self._render_generation += 1
        self._render_requested = None
        self._raster_cache.clear()
        self._displayed_base_key = None
        self._displayed_overlay_key = None
        # A running NumPy render is allowed to finish; its generation
        # token makes it impossible to display on a changed FITS dataset.
        self._dataset = None
        self.base_item.setPixmap(QPixmap())
        self.overlay_item.setPixmap(QPixmap())
        for item in self._overlays:
            self.scene.removeItem(item)
        self._overlays.clear()
        self.pixel_info.setText(
            "The selected field or band changed. Reload the original FITS.")
        self.context.setText("No local science data loaded.")
        self.scene.setSceneRect(0, 0, 0, 0)
        self.render_status.setText("No original FITS render loaded.")
        self.scale_bar.clear()

    def set_dataset(self, dataset: dict):
        if dataset["status"] != "DIAGNOSTIC_NOT_ADOPTED":
            raise ValueError("Interactive viewer requires diagnostic-only input")
        shape = dataset["pixel_shape"]
        if (len(shape) != 2 or any(v <= 0 for v in shape) or
                any(np.shape(a) != tuple(shape)
                    for a in dataset["data"].values())):
            raise ValueError("All original FITS layers must share a 2D pixel grid")
        # Until a tiled explorer is adopted, refuse a footprint that would
        # require a dangerously large full-resolution Qt raster. Never
        # silently downsample a pixel-accuracy scientific inspection.
        if int(shape[0]) * int(shape[1]) > 6_000_000:
            raise ValueError(
                "FITS cutout exceeds the 6 million-pixel full-resolution "
                "viewer limit; select a narrower field. Tiled mode is pending.")
        self.clear_data()
        self._dataset = dataset
        ny, nx = shape
        self.scene.setSceneRect(0, 0, nx, ny)
        self.context.setText(
            f"{dataset['source']}  ·  "
            f"original HDU {dataset['hdu_indices']}  ·  "
            f"{nx} × {ny} px  ·  {dataset['field'].width_arcmin:g}′ square")
        self._add_outlines()
        self._show_layer()
        self.view.fit_image()

    @staticmethod
    def _pixmap(rgb: np.ndarray, *, alpha=False) -> QPixmap:
        if rgb.dtype != np.uint8:
            raise ValueError("Display data must be UINT8")
        expected = 4 if alpha else 3
        if rgb.ndim != 3 or rgb.shape[2] != expected:
            raise ValueError("Expected RGB or RGBA display array")
        # FITS y+ is upward; QImage top-to-bottom is downward.
        qt_array = np.ascontiguousarray(np.flipud(rgb))
        height, width = qt_array.shape[:2]
        fmt = (QImage.Format_RGBA8888 if alpha else QImage.Format_RGB888)
        image = QImage(
            qt_array.data, width, height, width*expected, fmt).copy()
        return QPixmap.fromImage(image)

    def _layer_changed(self, *_):
        kind = self.layer.currentData()
        numeric = kind in ("image", "invvar", "nexp")
        self.contrast.setEnabled(kind == "image")
        self.scaling.setEnabled(numeric)
        self.vmin.setEnabled(numeric)
        self.vmax.setEnabled(numeric)
        self.apply_limits.setEnabled(numeric)
        self.auto_limits.setEnabled(numeric)
        # Reject incompatible transfer choices in the widget itself,
        # instead of allowing a background thread to crash repeatedly.
        if numeric:
            try:
                transfer_settings(kind, scaling=self.scaling.currentData())
            except ValueError:
                self.scaling.setCurrentIndex(0)
        saved = self._manual_ranges.get(kind)
        self.vmin.setText(format(saved[0], ".8g") if saved else "")
        self.vmax.setText(format(saved[1], ".8g") if saved else "")
        self._show_layer()

    def _scaling_changed(self, *_):
        kind = self.layer.currentData()
        saved = self._manual_ranges.get(kind)
        if kind in ("image", "invvar", "nexp"):
            try:
                transfer_settings(
                    kind, scaling=self.scaling.currentData(), limits=saved)
            except ValueError as exc:
                # Switching a display transfer cannot turn an originally
                # valid view into a stale/undefined logarithmic transfer.
                self._manual_ranges.pop(kind, None)
                self.vmin.clear()
                self.vmax.clear()
                try:
                    transfer_settings(kind, scaling=self.scaling.currentData())
                except ValueError:
                    self.scaling.blockSignals(True)
                    self.scaling.setCurrentIndex(0)
                    self.scaling.blockSignals(False)
                self.range_info.setText(f"Scale adjusted: {exc}")
        self._show_layer()

    def _apply_display_range(self):
        kind = self.layer.currentData()
        if kind not in ("image", "invvar", "nexp"):
            return
        try:
            lo = float(self.vmin.text().strip())
            hi = float(self.vmax.text().strip())
            _, accepted = transfer_settings(
                kind, scaling=self.scaling.currentData(), limits=(lo, hi))
        except (ValueError, TypeError) as exc:
            self.range_info.setText(f"Display range rejected: {exc}")
            return
        self._manual_ranges[kind] = accepted
        self._show_layer()

    def _auto_display_range(self):
        kind = self.layer.currentData()
        self._manual_ranges.pop(kind, None)
        self.vmin.clear()
        self.vmax.clear()
        self._show_layer()

    def _render_keys(self):
        kind = self.layer.currentData()
        contrast = self.contrast.currentData()
        scaling = self.scaling.currentData()
        limits = self._manual_ranges.get(kind)
        flags = tuple(
            self.flags[key].isChecked()
            for key in ("SATUR", "ALLMASK", "BRIGHT",
                        "MEDIUM", "GALAXY", "INVALID"))
        # For the categorical quality view, overlay controls are not applied:
        # this prevents hiding the QA categories behind another QA overlay.
        overlay_key = ("overlay", "transparent") if kind == "quality" else (
            "overlay", *flags)
        return ("base", kind, contrast, scaling, limits), overlay_key

    @staticmethod
    def _compute_display(dataset, kind, contrast, flags, base, overlay,
                         scaling="auto", limits=None):
        """Worker-safe; computes only NumPy arrays and metadata, no Qt."""
        valid_layer = kind in ("image", "invvar", "nexp")
        if valid_layer:
            lo, hi, mode = display_range(
                dataset["data"][kind], kind, contrast=contrast,
                scaling=scaling, limits=limits)
            info = (lo, hi, mode)
        else:
            info = None
        if base is None:
            previews = dataset.get("previews", {})
            if (kind != "quality" and contrast == "standard" and
                    scaling == "auto" and limits is None and kind in previews):
                base = previews[kind]
            else:
                base = rgb_for_layer(
                    dataset["data"]["maskbits"] if kind == "quality"
                    else dataset["data"][kind],
                    kind, dataset["band"], contrast=contrast,
                    scaling=scaling, limits=limits,
                    numeric_layers=dataset["data"] if kind == "quality" else None)
        if overlay is None:
            if kind == "quality":
                overlay = np.zeros((*dataset["pixel_shape"], 4), dtype=np.uint8)
            else:
                data = dataset["data"]
                overlay = overlay_rgba(
                    data["maskbits"], dataset["band"],
                    show_satur=flags[0], show_allmask=flags[1],
                    show_bright=flags[2], show_medium=flags[3],
                    show_galaxy=flags[4], show_invalid=flags[5],
                    image=data["image"], invvar=data["invvar"],
                    nexp=data["nexp"])
        return base, overlay, info

    def _show_layer(self, *_):
        self._request_render()

    def _set_smooth_preview(self, enabled: bool):
        mode = Qt.SmoothTransformation if enabled else Qt.FastTransformation
        self.base_item.setTransformationMode(mode)
        self.overlay_item.setTransformationMode(mode)

    def _show_overlay(self, *_):
        self._request_render()

    def _request_render(self):
        """Coalesce rapid edits: only the last generation is published."""
        if self._dataset is None:
            return
        self._render_generation += 1
        generation = self._render_generation
        base_key, overlay_key = self._render_keys()
        self._render_requested = (generation, self._dataset,
                                  base_key, overlay_key)
        if self._render_future is None:
            self._start_render()

    def _start_render(self):
        request = self._render_requested
        if request is None or self._dataset is None:
            self._render_timer.stop()
            return
        self._render_requested = None
        generation, dataset, base_key, overlay_key = request
        _, kind, contrast, scaling, limits = base_key
        flags = (False, False, False, False, False, False) if kind == "quality" else overlay_key[1:]
        base = self._raster_cache.get(base_key)
        overlay = self._raster_cache.get(overlay_key)
        self._render_active = (generation, dataset, base_key, overlay_key)
        # Small fixtures remain immediately inspectable in Qt tests.
        # Large original data are converted to display RGB off the UI thread.
        if dataset["pixel_shape"][0] * dataset["pixel_shape"][1] <= 120_000:
            try:
                rgb, rgba, info = self._compute_display(
                    dataset, kind, contrast, flags, base, overlay,
                    scaling=scaling, limits=limits)
            except Exception as exc:
                self.render_status.setText(
                    f"Display rendering failed: {type(exc).__name__}: {exc}")
            else:
                self._finish_render(
                    generation, dataset, base_key, overlay_key, rgb, rgba,
                    info)
            self._render_active = None
            if self._render_requested is not None:
                self._start_render()
            return
        self.render_status.setText(
            "Preparing display in background · original pixels unchanged…")
        self._render_future = self._render_executor.submit(
            self._compute_display, dataset, kind, contrast,
            flags, base, overlay, scaling, limits)
        self._render_timer.start()

    def _poll_render(self):
        future = self._render_future
        if future is None or not future.done():
            return
        self._render_future = None
        generation, dataset, base_key, overlay_key = self._render_active
        self._render_active = None
        try:
            rgb, rgba, info = future.result()
        except Exception as exc:
            if generation == self._render_generation:
                self.render_status.setText(
                    f"Display rendering failed: {type(exc).__name__}: {exc}")
        else:
            self._finish_render(
                generation, dataset, base_key, overlay_key, rgb, rgba,
                info)
        if self._render_requested is not None:
            self._start_render()
        else:
            self._render_timer.stop()

    def _finish_render(self, generation, dataset, base_key, overlay_key,
                       rgb, rgba, info):
        if generation != self._render_generation or dataset is not self._dataset:
            return
        self._raster_cache.put(base_key, rgb)
        self._raster_cache.put(overlay_key, rgba)
        if self._displayed_base_key != base_key:
            self.base_item.setPixmap(self._pixmap(rgb))
            self._displayed_base_key = base_key
        if self._displayed_overlay_key != overlay_key:
            self.overlay_item.setPixmap(self._pixmap(rgba, alpha=True))
            self._displayed_overlay_key = overlay_key
        kind = base_key[1]
        if info is None:
            self.scale_bar.clear()
            if kind == "quality":
                self.range_info.setText(
                    "QUALITY · categorical: valid / warning / SATUR / "
                    "ALLMASK / numeric invalid · no science adoption")
            else:
                self.range_info.setText("MASKBITS · categorical bitmask display")
        else:
            lo, hi, mode = info
            unit = ("nanomaggy" if kind == "image" else
                    "1/nanomaggy²" if kind == "invvar" else "exposures")
            additional = (" · nonpositive IMAGE hidden by log display"
                          if kind == "image" and mode == "log" else "")
            self.range_info.setText(
                f"{kind.upper()} · {mode} · {lo:.6g} … {hi:.6g} "
                f"{unit}{additional} · display only")
            # Display-only 256-sample color bar computed from identical
            # native-unit bounds; cheap enough to render on the Qt thread.
            samples = np.linspace(lo, hi, 256, dtype=np.float64)[None, :]
            bar = rgb_for_layer(
                samples, kind, dataset["band"], contrast=base_key[2],
                scaling=mode, limits=(lo, hi))
            self.scale_bar.setPixmap(self._pixmap(bar).scaled(
                256, 12, Qt.IgnoreAspectRatio, Qt.FastTransformation))
        self.render_status.setText(
            "Full-resolution original pixels · "
            f"{dataset['pixel_shape'][1]} × {dataset['pixel_shape'][0]} · "
            f"RGB cache {self._raster_cache.bytes_used / (1024*1024):.1f} / "
            f"{self._raster_cache.max_bytes / (1024*1024):.0f} MiB · "
            "display only")

    def closeEvent(self, event):
        self._render_timer.stop()
        self._render_generation += 1
        self._render_requested = None
        self._render_executor.shutdown(wait=False, cancel_futures=True)
        super().closeEvent(event)

    def _add_outlines(self):
        self._overlays.clear()
        ny = self._dataset["pixel_shape"][0]
        styles = {
            "square": (QColor("#E45EA8"), Qt.SolidLine, 1.4),
            "target": (QColor("#D6F38E"), Qt.DashLine, 1.1),
            "sky_inner": (QColor("#F6D06F"), Qt.DotLine, 1.0),
            "sky_outer": (QColor("#F6D06F"), Qt.DotLine, 1.0),
        }
        for key, points in self._dataset["outlines"].items():
            if not points:
                continue
            path = QPainterPath(QPointF(points[0][0]+.5, ny-1-points[0][1]+.5))
            for x, y in points[1:]:
                path.lineTo(x+.5, ny-1-y+.5)
            path.closeSubpath()
            color, line, width = styles[key]
            item = QGraphicsPathItem(path)
            pen = QPen(color, width, line)
            pen.setCosmetic(True)
            item.setPen(pen)
            item.setZValue(2)
            self.scene.addItem(item)
            self._overlays.append(item)
        self._toggle_outlines()

    def _toggle_outlines(self, *_):
        show = self.flags["OUTLINES"].isChecked()
        for item in self._overlays:
            item.setVisible(show)

    def _fit(self):
        self.view.fit_image()

    def inspect_pixel(self, x_scene: float, y_scene: float):
        if self._dataset is None:
            return
        pixel = fits_pixel_from_scene(
            x_scene, y_scene, self._dataset["pixel_shape"])
        if pixel is None:
            self.pixel_info.setText("Outside the original FITS cutout.")
            return
        x, y = pixel
        data = self._dataset["data"]
        ra, dec = self._dataset["wcs"].all_pix2world(float(x), float(y), 0)
        bits = int(maskbits_to_uint(data["maskbits"][y:y+1, x:x+1])[0, 0])
        labels = [
            name for name, number in LEGACY_BITS.items()
            if bits & (1 << number)
        ]
        flags = ", ".join(labels) if labels else "none"
        self.pixel_info.setText(
            f"Pixel ({x}, {y})  ·  ICRS {ra:.7f}°, {dec:+.7f}°   "
            f"IMAGE {float(data['image'][y,x]):.5g} nanomaggy   "
            f"INVVAR {float(data['invvar'][y,x]):.5g}   "
            f"NEXP {float(data['nexp'][y,x]):g}   "
            f"MASKBITS {bits}: {flags}")
