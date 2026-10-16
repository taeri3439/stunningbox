"""Input normalization for non-PDF uploads.

Two distinct paths, because the formats behave very differently:

- **Word (.doc):** LibreOffice converts it to .docx cleanly, and docx_extractor then
  reads the native structure (the office/Path-B route). (.docx needs no conversion.)
- **HWP (.hwp):** LibreOffice CANNOT load real Korean .hwp files (its libhwp filter
  fails with "source file could not be loaded"). pyhwp reads them, so we extract a
  semantic XHTML+CSS rendering with `hwp5html` and parse it directly into the semantic
  model (see `hwp_extractor`) — no LibreOffice, no vision. The XHTML already carries the
  page geometry, fonts, alignment, tables and images we need.
  (Verified on real KFI .hwp samples: LibreOffice-direct fails; hwp5html works.)

LibreOffice runs as one-shot subprocesses; each call gets an isolated user profile so
concurrent conversions never share (and corrupt) one profile.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import uuid

# Formats LibreOffice converts to .docx for the native office path. .docx is already
# the target (no-op); .hwp is handled separately by hwp_to_pdf (LibreOffice can't read it).
_CONVERTIBLE = {".doc"}
SOFFICE_BIN = os.environ.get("SOFFICE_BIN", "soffice")


def needs_conversion(src_path: str) -> bool:
    return os.path.splitext(src_path)[1].lower() in _CONVERTIBLE


def _profile_url(profile_dir: str) -> str:
    # file:// + absolute path. Linux "/tmp/p" → file:///tmp/p ;
    # Windows "C:\\p" → file:///C:/p . Both work after slashing + lstrip.
    return "file:///" + profile_dir.replace(os.sep, "/").lstrip("/")


def soffice_cmd(
    src_path: str,
    out_dir: str,
    profile_dir: str,
    target: str = "docx",
    soffice: str = SOFFICE_BIN,
) -> list[str]:
    """Build the LibreOffice headless argv converting `src_path` to `target` (e.g.
    "docx" or "pdf") in `out_dir`, isolated to `profile_dir` (concurrency-safe)."""
    return [
        soffice,
        "--headless",
        "--convert-to", target,
        "--outdir", out_dir,
        f"-env:UserInstallation={_profile_url(profile_dir)}",
        src_path,
    ]


def _run_soffice(src_path: str, out_dir: str, target: str) -> str:
    """Run a one-shot LibreOffice conversion and return the produced file's path."""
    os.makedirs(out_dir, exist_ok=True)
    profile_dir = os.path.join(tempfile.gettempdir(), f"lo_profile_{uuid.uuid4().hex}")
    cmd = soffice_cmd(src_path, out_dir, profile_dir, target=target)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
    if proc.returncode != 0:
        raise RuntimeError(
            f"LibreOffice {target} conversion failed for {os.path.basename(src_path)} "
            f"(exit {proc.returncode}): {proc.stderr.strip()}"
        )
    out_ext = "." + target.split(":")[0]
    out_path = os.path.join(
        out_dir, os.path.splitext(os.path.basename(src_path))[0] + out_ext
    )
    if not os.path.exists(out_path):
        raise RuntimeError(
            f"LibreOffice reported success but produced no {out_ext} at {out_path}. "
            f"stdout: {proc.stdout.strip()}"
        )
    return out_path


def to_docx(src_path: str, out_dir: str) -> str:
    """Return `src_path` as a .docx path. .docx is returned unchanged; .doc is converted
    with LibreOffice. Raises ValueError for unsupported formats (HWP goes via
    hwp_to_pdf, not here) and RuntimeError if conversion fails."""
    ext = os.path.splitext(src_path)[1].lower()
    if ext == ".docx":
        return src_path
    if ext not in _CONVERTIBLE:
        raise ValueError(f"Unsupported office format for to_docx: {ext}")
    return _run_soffice(src_path, out_dir, "docx")


def _hwp5html_bin() -> str:
    """Locate the pyhwp `hwp5html` console script (installed beside the interpreter)."""
    bindir = os.path.dirname(sys.executable)
    for name in ("hwp5html.exe", "hwp5html"):
        cand = os.path.join(bindir, name)
        if os.path.exists(cand):
            return cand
    found = shutil.which("hwp5html")
    if found:
        return found
    raise RuntimeError("hwp5html not found — is pyhwp installed in this environment?")


def hwp_to_html(src_path: str, out_dir: str) -> str:
    """Render a Korean .hwp to hwp5html output for the native HWP extractor.

    pyhwp's `hwp5html` reads the binary .hwp directly (LibreOffice can't) and writes a
    semantic `index.xhtml` + `styles.css` + `bindata/` into `<out_dir>/hwphtml`. Returns
    that directory. No LibreOffice round-trip — `hwp_extractor` parses the XHTML+CSS.
    """
    os.makedirs(out_dir, exist_ok=True)
    html_dir = os.path.join(out_dir, "hwphtml")
    proc = subprocess.run(
        [_hwp5html_bin(), "--output", html_dir, src_path],
        capture_output=True, text=True, timeout=240,
    )
    xhtml = os.path.join(html_dir, "index.xhtml")
    if proc.returncode != 0 or not os.path.exists(xhtml):
        raise RuntimeError(
            f"hwp5html failed for {os.path.basename(src_path)} "
            f"(exit {proc.returncode}): {proc.stderr.strip()}"
        )
    return html_dir
