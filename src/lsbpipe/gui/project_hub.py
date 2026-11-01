"""Offline-first startup Project Hub and object creation wizard."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QFrame,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QMessageBox, QPushButton, QStackedWidget, QVBoxLayout,
    QWidget, QDoubleSpinBox, QApplication,
)

from lsbpipe.surveys.legacy import SkyField
from lsbpipe.surveys.object_projects import (
    create_object_project, import_object_project, read_object_project,
    recent_projects, relink_object_project, touch_object_project,
)
from .survey_explorer import SurveyExplorer


class CreateProjectDialog(QDialog):
    """Three-step wizard; no ICRS position or original FITS required."""
    def __init__(self, parent=None, *, default_root: Path | None = None):
        super().__init__(parent)
        self.setWindowTitle("Create galaxy project · LSBpipe")
        self.setMinimumWidth(500)
        self.project = None
        self.pages = QStackedWidget(self)
        self.name = QLineEdit()
        self.name.setObjectName("project_new_name")
        self.name.setPlaceholderText("Galaxy designation or working name")
        self.ra = QLineEdit()
        self.ra.setObjectName("project_new_ra")
        self.ra.setPlaceholderText("Optional · ICRS degrees")
        self.dec = QLineEdit()
        self.dec.setObjectName("project_new_dec")
        self.dec.setPlaceholderText("Optional · ICRS degrees")
        self.width_arcmin = QDoubleSpinBox()
        self.width_arcmin.setRange(0.1, 120.)
        self.width_arcmin.setValue(5.)
        self.width_arcmin.setSuffix(" arcmin")
        self.directory = QLineEdit()
        self.directory.setObjectName("project_new_root")
        self.parent_root = default_root or Path.home() / "LSBpipeProjects"
        self.description = QLabel()
        self.description.setWordWrap(True)

        first = QWidget()
        form = QFormLayout(first)
        form.addRow(QLabel("Object identity · step 1 of 3"))
        form.addRow("Project name", self.name)
        form.addRow("RA", self.ra)
        form.addRow("Dec", self.dec)
        form.addRow("Search field", self.width_arcmin)
        form.addRow(QLabel(
            "Unknown objects are supported. Leave both coordinates blank "
            "and specify them before a spatial analysis."))
        self.pages.addWidget(first)
        second = QWidget()
        form2 = QFormLayout(second)
        form2.addRow(QLabel("Storage · step 2 of 3"))
        row = QHBoxLayout()
        row.addWidget(self.directory, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        form2.addRow("Project directory", row)
        form2.addRow(QLabel(
            "Creates metadata only. Existing science FITS can be linked later; "
            "none are downloaded or modified here."))
        self.pages.addWidget(second)
        third = QWidget()
        v = QVBoxLayout(third)
        v.addWidget(QLabel("Review · step 3 of 3"))
        v.addWidget(self.description)
        v.addStretch()
        self.pages.addWidget(third)

        self.back = QPushButton("Back")
        self.back.clicked.connect(self._previous)
        self.next = QPushButton("Next")
        self.next.setObjectName("project_wizard_next")
        self.next.clicked.connect(self._advance)
        self.cancel = QPushButton("Cancel")
        self.cancel.clicked.connect(self.reject)
        controls = QHBoxLayout()
        controls.addWidget(self.cancel)
        controls.addStretch()
        controls.addWidget(self.back)
        controls.addWidget(self.next)
        layout = QVBoxLayout(self)
        heading = QLabel("New galaxy research project")
        heading.setStyleSheet("font-weight: 600; font-size: 18px;")
        layout.addWidget(heading)
        layout.addWidget(self.pages, 1)
        layout.addLayout(controls)
        self.name.textChanged.connect(self._suggest_directory)
        self._update_controls()

    def _suggest_directory(self, _=None):
        if getattr(self, "_directory_user_edited", False):
            return
        value = self.name.text().strip()
        safe = "".join(
            ch if ch.isalnum() or ch in ("_", "-", " ") else "_"
            for ch in value).strip().replace(" ", "_")
        self.directory.setText(str(self.parent_root / safe) if safe else "")
        
    def _browse(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Choose parent folder", str(self.parent_root))
        if folder:
            self._directory_user_edited = True
            self.directory.setText(str(Path(folder) / (
                self.name.text().strip().replace(" ", "_") or "NewGalaxy")))

    def _field(self) -> SkyField | None:
        ra, dec = self.ra.text().strip(), self.dec.text().strip()
        if not ra and not dec:
            return None
        if not ra or not dec:
            raise ValueError("Provide both RA and Dec or leave both empty")
        return SkyField(float(ra), float(dec), self.width_arcmin.value())

    def _update_controls(self):
        page = self.pages.currentIndex()
        self.back.setEnabled(page > 0)
        self.next.setText("Create project" if page == 2 else "Next")
        if page == 2:
            field = self._field()
            pos = ("Coordinates: not specified yet" if field is None else
                   f"ICRS: {field.ra_deg:.8f}°, {field.dec_deg:+.8f}°")
            self.description.setText(
                f"Object: {self.name.text().strip()}\n{pos}\n"
                f"Location: {self.directory.text().strip()}\n\n"
                "The project is created locally. No SIMBAD request, "
                "survey search or file download is triggered.")

    def _previous(self):
        self.pages.setCurrentIndex(max(0, self.pages.currentIndex() - 1))
        self._update_controls()

    def _advance(self):
        page = self.pages.currentIndex()
        try:
            if page == 0:
                name = self.name.text().strip()
                if not name:
                    raise ValueError("Enter a galaxy/project name")
                self._field()
            if page == 1:
                folder = self.directory.text().strip()
                if not folder:
                    raise ValueError("Choose a project directory")
                folder = Path(folder).expanduser()
                if folder.exists() and any(folder.iterdir()):
                    raise FileExistsError(
                        "Folder already contains files. Use Import existing project.")
            if page == 2:
                self.project = create_object_project(
                    self.name.text().strip(), self.directory.text().strip(),
                    field=self._field())
                self.accept()
                return
            self.pages.setCurrentIndex(page + 1)
            self._update_controls()
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "Cannot create project", str(exc))


class ProjectHub(QMainWindow):
    """Launch surface. Explorer is instantiated only for a selected project."""
    projectOpened = Signal(str)

    def __init__(self):
        super().__init__()
        self.setObjectName("lsbpipe_project_hub")
        self.setWindowTitle("LSBpipe · Galaxy Projects")
        self.resize(960, 650)
        self.workspace = None
        content = QWidget()
        outer = QVBoxLayout(content)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(16)
        heading = QLabel("LSBpipe")
        heading.setStyleSheet("font-size: 28px; font-weight: 700")
        subheading = QLabel(
            "Galaxy research projects · local-first, reproducible analysis")
        subheading.setStyleSheet("font-size: 13px; color: palette(mid)")
        outer.addWidget(heading)
        outer.addWidget(subheading)
        actions = QHBoxLayout()
        self.new_btn = QPushButton("New galaxy project")
        self.new_btn.setObjectName("hub_new_project")
        self.new_btn.clicked.connect(self._new_project)
        self.import_btn = QPushButton("Import existing folder…")
        self.import_btn.setObjectName("hub_import_project")
        self.import_btn.clicked.connect(self._import_dialog)
        self.continue_btn = QPushButton("Continue selected")
        self.continue_btn.setObjectName("hub_continue")
        self.continue_btn.clicked.connect(self._open_selected)
        actions.addWidget(self.new_btn)
        actions.addWidget(self.import_btn)
        actions.addStretch()
        actions.addWidget(self.continue_btn)
        outer.addLayout(actions)
        outer.addWidget(QLabel("Recent projects"))
        self.filter = QLineEdit()
        self.filter.setObjectName("hub_project_filter")
        self.filter.setPlaceholderText("Search name, path or identifier…")
        self.filter.textChanged.connect(self._filter_rows)
        outer.addWidget(self.filter)
        self.projects = QListWidget()
        self.projects.setObjectName("hub_project_list")
        self.projects.itemDoubleClicked.connect(lambda _: self._open_selected())
        self.projects.itemSelectionChanged.connect(self._selection_changed)
        outer.addWidget(self.projects, 1)
        self.hint = QLabel(
            "No project is opened automatically. Choose a recent object, "
            "create an empty project, or import existing Legacy FITS.")
        self.hint.setWordWrap(True)
        outer.addWidget(self.hint)
        self.setCentralWidget(content)
        self.refresh()

    def refresh(self):
        self.projects.clear()
        try:
            rows = recent_projects()
        except (ValueError, OSError) as exc:
            self.hint.setText(
                f"Project library index could not be read: {exc}. "
                "Existing project folders are untouched; import them again.")
            rows = []
        for record in rows:
            exists = bool(record["available"])
            name = record.get("display_name") or "Unnamed"
            state = "On disk" if exists else "Missing · relink required"
            item = QListWidgetItem(f"{name}    ·    {state}\n{record['root']}")
            item.setData(Qt.UserRole, record)
            item.setToolTip(record["root"])
            self.projects.addItem(item)
        self._filter_rows()
        if self.projects.count():
            self.projects.setCurrentRow(0)
        self._selection_changed()

    def _filter_rows(self):
        query = self.filter.text().strip().casefold()
        for index in range(self.projects.count()):
            item = self.projects.item(index)
            item.setHidden(query not in item.text().casefold())

    def _selection_changed(self):
        item = self.projects.currentItem()
        self.continue_btn.setEnabled(bool(
            item and not item.isHidden() and item.data(Qt.UserRole)["available"]))

    def _new_project(self):
        dialog = CreateProjectDialog(self)
        if dialog.exec() == QDialog.Accepted and dialog.project is not None:
            self._enter_project(dialog.project)

    def _import_dialog(self):
        directory = QFileDialog.getExistingDirectory(
            self, "Import existing LSBpipe or Legacy FITS folder",
            str(Path.home()))
        if directory:
            self.import_folder(directory)

    def import_folder(self, directory: str | Path, *,
                      fallback_field: SkyField | None = None) -> bool:
        try:
            project = import_object_project(
                directory, fallback_field=fallback_field)
        except (OSError, ValueError, KeyError) as exc:
            self.hint.setText(f"Import failed: {exc}")
            QMessageBox.warning(self, "Import was not completed", str(exc))
            return False
        self._enter_project(project)
        return True

    def _open_selected(self):
        item = self.projects.currentItem()
        if not item or item.isHidden():
            return
        row = item.data(Qt.UserRole)
        if not row["available"]:
            folder = QFileDialog.getExistingDirectory(
                self, "Relink moved project", str(Path.home()))
            if folder:
                try:
                    project = relink_object_project(row["project_id"], folder)
                    self._enter_project(project)
                except (OSError, ValueError) as exc:
                    QMessageBox.warning(self, "Cannot relink project", str(exc))
            return
        try:
            self._enter_project(read_object_project(row["root"]))
        except (OSError, ValueError, KeyError) as exc:
            QMessageBox.warning(self, "Cannot open project", str(exc))

    def _enter_project(self, project):
        if self.workspace is not None:
            return
        touch_object_project(project)
        self.workspace = SurveyExplorer(restore_session=False, launch_remote=False)
        self.workspace.attach_object_project(project)
        self.workspace.returnToProjects.connect(self._return_from_workspace)
        self.workspace.show()
        self.hide()
        self.projectOpened.emit(project.project_id)

    def _return_from_workspace(self):
        window = self.workspace
        if window is None:
            return
        if window.thread is not None:
            QMessageBox.information(
                window, "Operation in progress",
                "Finish the running task before switching projects.")
            return
        window._returning_to_hub = True
        window._save_project_state()
        self.show()
        window.close()
        window.deleteLater()
        self.workspace = None
        self.refresh()


def main():
    app = QApplication.instance() or QApplication([])
    hub = ProjectHub()
    hub.show()
    return app.exec()
