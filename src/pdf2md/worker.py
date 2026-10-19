"""Background worker that runs the batch conversion without blocking the GUI."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from .converter import ConversionOptions, convert_pdf_to_file


@dataclass
class JobItem:
    src: Path
    dst: Path
    status: str = "Queued"  # Queued, Converting, Done, Failed
    detail: str = ""


class BatchWorker(QObject):
    item_started = pyqtSignal(int)
    item_progress = pyqtSignal(int, int, int)      # index, current_page, total_pages
    item_finished = pyqtSignal(int, bool, str)      # index, success, message
    batch_finished = pyqtSignal(int, int)           # succeeded, failed
    log_line = pyqtSignal(str)

    def __init__(self, jobs: list[JobItem], opts: ConversionOptions):
        super().__init__()
        self._jobs = jobs
        self._opts = opts
        self._cancel_requested = False

    def cancel(self):
        self._cancel_requested = True

    def run(self):
        succeeded = 0
        failed = 0
        for idx, job in enumerate(self._jobs):
            if self._cancel_requested:
                self.log_line.emit("Batch cancelled by user.")
                break

            self.item_started.emit(idx)
            self.log_line.emit(f"Converting: {job.src.name}")

            def progress_cb(cur, total, i=idx):
                self.item_progress.emit(i, cur, total)

            try:
                result = convert_pdf_to_file(job.src, job.dst, self._opts, progress_cb)
                if result.warnings:
                    for w in result.warnings:
                        self.log_line.emit(f"  [{job.src.name}] {w}")
                msg = "Converted"
                if result.had_ocr:
                    msg += " (OCR used on some pages)"
                self.item_finished.emit(idx, True, msg)
                self.log_line.emit(f"  Done: {job.dst.name}")
                succeeded += 1
            except Exception as exc:  # noqa: BLE001 - surface any failure to the GUI
                self.item_finished.emit(idx, False, str(exc))
                self.log_line.emit(f"  FAILED: {job.src.name} — {exc}")
                failed += 1

        self.batch_finished.emit(succeeded, failed)


def make_thread(jobs: list[JobItem], opts: ConversionOptions) -> tuple[QThread, BatchWorker]:
    thread = QThread()
    worker = BatchWorker(jobs, opts)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    return thread, worker
