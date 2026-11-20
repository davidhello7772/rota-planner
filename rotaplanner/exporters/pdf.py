"""PDF files, made by Edge, Chrome or Chromium already on the computer (Edge
is always there on Windows), run headless on a local HTML file. Nothing is
sent anywhere.

Other Chromium-based browsers (Vivaldi, Brave from a snap…) hang in headless
mode, so they are not used. Without a suitable browser, documents are
printed from the browser's own Print dialog ("Save as PDF").
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import List, Optional

TIMEOUT_SECONDS = 45


class PdfError(Exception):
    pass


def _candidates() -> List[str]:
    names = ["microsoft-edge", "microsoft-edge-stable", "google-chrome", "google-chrome-stable", "chromium", "chromium-browser"]
    # A snap cannot read the temporary folder, so a browser installed as a snap is left out.
    found = [path for path in (shutil.which(n) for n in names) if path and not path.startswith("/snap/")]
    if sys.platform == "darwin":
        apps = ["Microsoft Edge", "Google Chrome", "Chromium"]
        found += [f"/Applications/{app}.app/Contents/MacOS/{app}" for app in apps]
    elif os.name == "nt":
        roots = [os.environ.get(k, "") for k in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA")]
        tails = [r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe"]
        found += [str(Path(root) / tail) for root in roots if root for tail in tails]
    return found


def find_browser() -> Optional[str]:
    """A browser able to print to PDF; ``ROTA_PDF_BROWSER`` can name one."""
    chosen = os.environ.get("ROTA_PDF_BROWSER")
    for candidate in ([chosen] if chosen else []) + _candidates():
        if candidate and Path(candidate).is_file():
            return candidate
    return None


def html_to_pdf(html: str, target: Path, browser: str) -> Path:
    """Write ``html`` as a PDF at ``target`` (replaced if it exists)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="rota-pdf-"))
    try:
        page = work / "page.html"
        page.write_text(html, encoding="utf-8")
        output = work / "out.pdf"
        command = [
            browser, "--headless", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
            "--disable-extensions", "--disable-sync", "--disable-background-networking",
            "--disable-component-update", "--disable-default-apps", "--metrics-recording-only",
            "--no-pdf-header-footer", "--print-to-pdf-no-header",
            f"--user-data-dir={work / 'profile'}", f"--print-to-pdf={output}", page.as_uri(),
        ]
        _run(command)
        if not output.exists() or output.stat().st_size == 0:
            raise PdfError("The browser did not make the PDF.")
        shutil.move(str(output), str(target))
    finally:
        shutil.rmtree(work, ignore_errors=True)  # the page holds names: do not leave it behind
    return target


def sweep_stale(max_age_seconds: int = 3600) -> None:
    """Remove work folders left by an export that was interrupted (they hold names)."""
    now = time.time()
    for folder in Path(tempfile.gettempdir()).glob("rota-pdf-*"):
        try:
            if now - folder.stat().st_mtime > max_age_seconds:
                shutil.rmtree(folder, ignore_errors=True)
        except OSError:
            continue


def _run(command: List[str]) -> None:
    """Run the browser; if it does not finish in time, stop it and everything it started."""
    windows = os.name == "nt"
    try:
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   start_new_session=not windows)
    except OSError as error:
        raise PdfError(f"The browser could not be started ({error}).") from error
    try:
        process.wait(timeout=TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        if windows:
            process.kill()
        else:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        raise PdfError("The browser took too long to make the PDF.") from None


def open_folder(folder: Path) -> None:
    """Show a folder in the computer's file manager."""
    folder.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        os.startfile(str(folder))  # type: ignore[attr-defined]  # Windows only
    else:
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        subprocess.Popen([opener, str(folder)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
