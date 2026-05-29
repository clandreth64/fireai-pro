"""
FireAI Pro — pdf_page_isolator.py
==================================
Tiny helper that extracts ONE page from a large PDF into a small temp file.

Why this exists
---------------
fireai_document_processor.py calls `pdfplumber.open(file_path)` twice on the
full uploaded PDF — once via _file_to_image() (line ~560) and once for vector
wall extraction (line ~102). On a 156 MB / 192-page architectural set
(e.g. a real Costco IFC drawing package), pdfplumber's full-document parse
spikes memory enough that Railway's kernel sends SIGKILL before any in-process
timeout can catch it. The container restarts mid-job and the design pipeline
silently dies.

This helper uses pypdf (pure Python, very lightweight) to peel off just the
floor-plan page into a temp file. Downstream code then opens THAT tiny file
with pdfplumber, which keeps peak memory tiny.

Usage
-----
    from pdf_page_isolator import single_page_pdf

    with single_page_pdf(big_pdf_path, page_index=64) as small_pdf:
        with pdfplumber.open(small_pdf) as pdf:
            page = pdf.pages[0]            # always page 0 of the mini-PDF
            lines = page.lines or []
            ...

If pypdf is unavailable or extraction fails, the helper falls back to
yielding the original path (so the caller's behavior is unchanged on
small/healthy PDFs). The behavior is identical for callers that already
work; only large PDFs are protected.

Drop this file at the repo root next to fireai_document_processor.py.
"""

from __future__ import annotations

import logging
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

log = logging.getLogger("fireai.page_isolator")


@contextmanager
def single_page_pdf(src_path: str, page_index: int = 0):
    """
    Yield a path to a tiny PDF containing only `page_index` from `src_path`.
    Falls back to yielding `src_path` if extraction fails for any reason.

    The mini-PDF is deleted when the with-block exits.
    """
    src_path = str(src_path)
    tmp_path = None
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError:
        log.warning("[PageIsolator] pypdf not installed — using full PDF "
                    "(memory risk on large sets)")
        yield src_path
        return

    try:
        reader = PdfReader(src_path)
        n = len(reader.pages)
        if n == 0:
            log.warning("[PageIsolator] %s has zero pages", src_path)
            yield src_path
            return

        idx = max(0, min(int(page_index), n - 1))
        writer = PdfWriter()
        writer.add_page(reader.pages[idx])

        fd, tmp_path = tempfile.mkstemp(suffix=".pdf", prefix="fireai_p_")
        with os.fdopen(fd, "wb") as f:
            writer.write(f)

        size_in  = os.path.getsize(src_path)
        size_out = os.path.getsize(tmp_path)
        log.info("[PageIsolator] %s page %d → %s (%.1f MB → %.2f MB)",
                 Path(src_path).name, idx + 1, Path(tmp_path).name,
                 size_in / 1e6, size_out / 1e6)

        yield tmp_path

    except Exception as exc:
        log.warning("[PageIsolator] Extraction failed (%s: %s) — using full PDF",
                    type(exc).__name__, exc)
        yield src_path
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
