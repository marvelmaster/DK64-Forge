"""Export-all helper shared by the tabs: pick a folder, export many entries, show progress.

Each tab passes a list of (label, job) pairs; ``job(folder)`` writes one entry. Failures are
collected and reported at the end instead of aborting the whole run; the progress dialog
can be cancelled between entries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Callable, Iterable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QProgressDialog, QWidget

Job = Callable[[Path], object]


@dataclass
class BatchResult:
    folder: Path
    done: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)
    cancelled: bool = False

    def summary(self) -> str:
        text = f"Exported {self.done} file(s) to {self.folder}"
        if self.failed:
            text += f"; {len(self.failed)} failed"
        if self.cancelled:
            text += " (cancelled)"
        return text


def safe_name(text: str, limit: int = 80) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")[:limit] or "entry"


def run_jobs(folder: Path, jobs: Iterable[tuple[str, Job]], progress=None) -> BatchResult:
    """Run every job into ``folder`` (Qt-free core, used by the dialog and the tests).

    ``progress(index, total, label)`` may return False to cancel."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    jobs = list(jobs)
    result = BatchResult(folder)
    for index, (label, job) in enumerate(jobs):
        if progress is not None and progress(index, len(jobs), label) is False:
            result.cancelled = True
            break
        try:
            job(folder)
            result.done += 1
        except Exception as exc:  # one broken entry must not stop the batch
            result.failed.append((label, str(exc)))
    return result


def export_all(parent: QWidget, title: str, jobs: list[tuple[str, Job]],
               folder: Path | None = None) -> BatchResult | None:
    """Ask for a folder (unless given), run the jobs with a cancellable progress dialog."""
    if not jobs:
        QMessageBox.information(parent, title, "There is nothing to export.")
        return None
    if folder is None:
        chosen = QFileDialog.getExistingDirectory(parent, f"{title}: choose a folder")
        if not chosen:
            return None
        folder = Path(chosen)
    dialog = QProgressDialog(title, "Cancel", 0, len(jobs), parent)
    dialog.setWindowModality(Qt.WindowModality.WindowModal)
    dialog.setMinimumDuration(300)

    def progress(index: int, total: int, label: str) -> bool:
        dialog.setValue(index)
        dialog.setLabelText(f"{title}\n{index + 1} of {total}: {label}")
        QApplication.processEvents()
        return not dialog.wasCanceled()

    try:
        result = run_jobs(folder, jobs, progress)
    finally:
        dialog.setValue(len(jobs))
        dialog.close()
    if result.failed:
        details = "\n".join(f"{label}: {error}" for label, error in result.failed[:12])
        more = f"\n... and {len(result.failed) - 12} more" if len(result.failed) > 12 else ""
        QMessageBox.warning(parent, title, f"{result.summary()}.\n\n{details}{more}")
    return result
