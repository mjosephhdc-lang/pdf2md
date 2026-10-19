"""Main GUI window: drag-and-drop batch PDF -> Markdown converter."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QThread
from PyQt6.QtGui import QDragEnterEvent, QDropEvent
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .converter import ConversionOptions
from .worker import BatchWorker, JobItem, make_thread

COLUMNS = ["File", "Status", "Output"]


class DropTable(QTableWidget):
    """A QTableWidget that also accepts dropped PDF files."""

    def __init__(self, on_files_dropped, parent=None):
        super().__init__(parent)
        self._on_files_dropped = on_files_dropped
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        paths = [Path(u.toLocalFile()) for u in event.mimeData().urls()]
        pdfs = [p for p in paths if p.suffix.lower() == ".pdf"]
        if pdfs:
            self._on_files_dropped(pdfs)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PDF to Markdown — Batch Converter")
        self.resize(820, 600)
        self.setAcceptDrops(True)

        self._jobs: list[JobItem] = []
        self._output_dir: Path | None = None
        self._thread: QThread | None = None
        self._worker: BatchWorker | None = None

        self._build_ui()

    # ---------- UI construction ----------

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        # File controls
        file_btn_row = QHBoxLayout()
        add_files_btn = QPushButton("Add Files…")
        add_files_btn.clicked.connect(self._on_add_files)
        add_folder_btn = QPushButton("Add Folder…")
        add_folder_btn.clicked.connect(self._on_add_folder)
        clear_btn = QPushButton("Clear List")
        clear_btn.clicked.connect(self._on_clear)
        file_btn_row.addWidget(add_files_btn)
        file_btn_row.addWidget(add_folder_btn)
        file_btn_row.addWidget(clear_btn)
        file_btn_row.addStretch(1)
        layout.addLayout(file_btn_row)

        hint = QLabel("Drag and drop PDF files anywhere in this window to add them.")
        hint.setStyleSheet("color: gray;")
        layout.addWidget(hint)

        # Table
        self.table = DropTable(self._add_paths)
        self.table.setColumnCount(len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self.table, stretch=1)

        # Output + options
        opts_box = QGroupBox("Output && Options")
        opts_layout = QVBoxLayout(opts_box)

        out_row = QHBoxLayout()
        out_row.addWidget(QLabel("Output folder:"))
        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("Same folder as each source PDF")
        out_row.addWidget(self.output_edit, stretch=1)
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._on_browse_output)
        out_row.addWidget(browse_btn)
        opts_layout.addLayout(out_row)

        checks_row = QHBoxLayout()
        self.ocr_check = QCheckBox("OCR scanned/image pages")
        self.ocr_check.setChecked(True)
        checks_row.addWidget(self.ocr_check)
        checks_row.addStretch(1)
        opts_layout.addLayout(checks_row)

        layout.addWidget(opts_box)

        # Progress + controls
        progress_row = QHBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        progress_row.addWidget(self.progress_bar, stretch=1)
        self.start_btn = QPushButton("Start Batch")
        self.start_btn.clicked.connect(self._on_start)
        progress_row.addWidget(self.start_btn)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self._on_cancel)
        self.cancel_btn.setEnabled(False)
        progress_row.addWidget(self.cancel_btn)
        layout.addLayout(progress_row)

        # Log
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)
        self.log.setFixedHeight(140)
        layout.addWidget(self.log)

    # ---------- Drag/drop on the whole window ----------

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        paths = [Path(u.toLocalFile()) for u in event.mimeData().urls()]
        pdfs = [p for p in paths if p.suffix.lower() == ".pdf"]
        if pdfs:
            self._add_paths(pdfs)

    # ---------- File list management ----------

    def _on_add_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Select PDF files", "", "PDF files (*.pdf)")
        if files:
            self._add_paths([Path(f) for f in files])

    def _on_add_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select folder containing PDFs")
        if folder:
            pdfs = sorted(Path(folder).glob("*.pdf"))
            if pdfs:
                self._add_paths(pdfs)
            else:
                QMessageBox.information(self, "No PDFs found", "No .pdf files were found in that folder.")

    def _add_paths(self, paths: list[Path]):
        existing = {j.src for j in self._jobs}
        for p in paths:
            if p in existing:
                continue
            dst = self._compute_output_path(p)
            job = JobItem(src=p, dst=dst)
            self._jobs.append(job)
            self._append_row(job)

    def _compute_output_path(self, src: Path) -> Path:
        out_dir = self._output_dir or src.parent
        return out_dir / (src.stem + ".md")

    def _append_row(self, job: JobItem):
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(job.src.name))
        self.table.setItem(row, 1, QTableWidgetItem(job.status))
        self.table.setItem(row, 2, QTableWidgetItem(str(job.dst)))

    def _on_clear(self):
        if self._thread and self._thread.isRunning():
            QMessageBox.warning(self, "Batch running", "Cancel the current batch before clearing the list.")
            return
        self._jobs.clear()
        self.table.setRowCount(0)
        self.progress_bar.setValue(0)

    def _on_browse_output(self):
        folder = QFileDialog.getExistingDirectory(self, "Select output folder")
        if folder:
            self._output_dir = Path(folder)
            self.output_edit.setText(folder)
            # Recompute destinations for already-queued jobs.
            for i, job in enumerate(self._jobs):
                job.dst = self._compute_output_path(job.src)
                self.table.setItem(i, 2, QTableWidgetItem(str(job.dst)))

    # ---------- Batch run ----------

    def _on_start(self):
        if not self._jobs:
            QMessageBox.information(self, "Nothing to do", "Add one or more PDF files first.")
            return
        if self._thread and self._thread.isRunning():
            return

        manual_out = self.output_edit.text().strip()
        if manual_out:
            self._output_dir = Path(manual_out)
            for i, job in enumerate(self._jobs):
                job.dst = self._compute_output_path(job.src)
                self.table.setItem(i, 2, QTableWidgetItem(str(job.dst)))

        for i, job in enumerate(self._jobs):
            job.status = "Queued"
            self.table.setItem(i, 1, QTableWidgetItem(job.status))

        opts = ConversionOptions(ocr_scanned_pages=self.ocr_check.isChecked())

        self.progress_bar.setValue(0)
        self.progress_bar.setMaximum(len(self._jobs) * 100)
        self.start_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.log.clear()
        self.log.appendPlainText(f"Starting batch: {len(self._jobs)} file(s).")

        self._thread, self._worker = make_thread(self._jobs, opts)
        self._worker.item_started.connect(self._on_item_started)
        self._worker.item_progress.connect(self._on_item_progress)
        self._worker.item_finished.connect(self._on_item_finished)
        self._worker.batch_finished.connect(self._on_batch_finished)
        self._worker.log_line.connect(self.log.appendPlainText)
        self._thread.start()

    def _on_cancel(self):
        if self._worker:
            self._worker.cancel()
            self.cancel_btn.setEnabled(False)

    def _on_item_started(self, idx: int):
        self._jobs[idx].status = "Converting"
        self.table.setItem(idx, 1, QTableWidgetItem("Converting…"))

    def _on_item_progress(self, idx: int, current: int, total: int):
        base = idx * 100
        pct = int((current / total) * 100) if total else 0
        self.progress_bar.setValue(base + pct)

    def _on_item_finished(self, idx: int, success: bool, message: str):
        status = message if success else f"Failed: {message}"
        self._jobs[idx].status = status
        self.table.setItem(idx, 1, QTableWidgetItem(status))
        self.progress_bar.setValue((idx + 1) * 100)

    def _on_batch_finished(self, succeeded: int, failed: int):
        self.start_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.log.appendPlainText(f"Batch complete: {succeeded} succeeded, {failed} failed.")
        if self._thread:
            self._thread.quit()
            self._thread.wait()
        QMessageBox.information(
            self, "Batch complete", f"{succeeded} file(s) converted successfully.\n{failed} failed."
        )


def run_app():
    import sys

    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
