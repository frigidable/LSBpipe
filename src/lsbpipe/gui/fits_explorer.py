"""Native PySide6 interactive viewer for *local original* Legacy FITS.

Astronomical FITS rows increase north/up; Qt images have a top-left
origin. This widget explicitly flips the display vertically and
undoes that flip when inspecting a pixel. World overlays use the
cutout WCS, not guessed pixel scales or a remote sky image.
"""
from __future__ import annotations

import numpy as np

from PySide6.QtCore import Qt, Signal, QPointF
from PySide6.QtGui import (
    QImage, QPixmap, QColor, QPainter, QPainterPath, QPen,
)
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QCheckBox,
    QPushButton, QGraphicsView, QGraphicsScene, QGraphicsPixmapItem,
    QGraphicsPathItem, QSizePolicy,
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
        ):
            self.layer.addItem(name, kind)
        bar.addWidget(self.layer, 2)
        bar.addWidget(QLabel("Stretch"))
        self.contrast = QComboBox()
        self.contrast.setObjectName("fits_contrast_selector")
        self.contrast.addItem("Standard", "standard")
        self.contrast.addItem("Faint features", "faint")
        bar.addWidget(self.contrast, 1)

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

        toggles = QHBoxLayout()
        self.flags = {}
        for key, text, enabled in (
            ("SATUR", "SATUR / ALLMASK", True),
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
            "Red: SATUR/ALLMASK  ·  Amber: Gaia MEDIUM  ·  "
            "Blue: SGA GALAXY  ·  Magenta hatch: invalid data. "
            "Display overlays only.")
        self.color_legend.setObjectName("fits_overlay_legend")
        self.color_legend.setWordWrap(True)
        layout.addWidget(self.color_legend)

        self.context = QLabel(
            "Open an original FITS quartet from the Science QA panel.")
        self.context.setObjectName("fits_provenance")
        self.context.setWordWrap(True)
        layout.addWidget(self.context)

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

        self.layer.currentIndexChanged.connect(self._show_layer)
        self.contrast.currentIndexChanged.connect(self._show_layer)
        for key in ("SATUR", "MEDIUM", "GALAXY", "INVALID"):
            self.flags[key].toggled.connect(self._show_overlay)
        self.smooth.toggled.connect(self._set_smooth_preview)
        self.flags["OUTLINES"].toggled.connect(self._toggle_outlines)
        self.view.pointHovered.connect(self.inspect_pixel)

    def clear_data(self):
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

    def set_dataset(self, dataset: dict):
        if dataset["status"] != "DIAGNOSTIC_NOT_ADOPTED":
            raise ValueError("Interactive viewer requires diagnostic-only input")
        shape = dataset["pixel_shape"]
        if (len(shape) != 2 or any(v <= 0 for v in shape) or
                any(np.shape(a) != tuple(shape)
                    for a in dataset["data"].values())):
            raise ValueError("All original FITS layers must share a 2D pixel grid")
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
        self._show_overlay()
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

    def _show_layer(self, *_):
        if self._dataset is None:
            return
        kind = self.layer.currentData()
        contrast = self.contrast.currentData()
        if contrast == "standard":
            rgb = self._dataset["previews"][kind]
        else:
            rgb = rgb_for_layer(
                self._dataset["data"][kind], kind,
                self._dataset["band"], contrast=contrast)
        self.base_item.setPixmap(self._pixmap(rgb))
        self._show_overlay()

    def _set_smooth_preview(self, enabled: bool):
        mode = Qt.SmoothTransformation if enabled else Qt.FastTransformation
        self.base_item.setTransformationMode(mode)
        self.overlay_item.setTransformationMode(mode)

    def _show_overlay(self, *_):
        if self._dataset is None:
            return
        alpha = overlay_rgba(
            self._dataset["data"]["maskbits"], self._dataset["band"],
            show_satur=self.flags["SATUR"].isChecked(),
            show_medium=self.flags["MEDIUM"].isChecked(),
            show_galaxy=self.flags["GALAXY"].isChecked(),
            show_invalid=self.flags["INVALID"].isChecked(),
            image=self._dataset["data"]["image"],
            invvar=self._dataset["data"]["invvar"],
            nexp=self._dataset["data"]["nexp"])
        self.overlay_item.setPixmap(self._pixmap(alpha, alpha=True))

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
