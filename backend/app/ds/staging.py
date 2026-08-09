"""Shared upload staging for the chat data-analysis paths.

Both DS chat engines start the same way: take the company's uploaded tabular
exports out of the dataset `raw/` dir and materialise them as plain CSVs in a
throwaway workdir — the deterministic v5.8 engine (`app.ds.chat_analysis`) only
globs `*.csv`, and the Claude code-execution path
(`app.ds.claude_analysis`) uploads the same staged files to the Anthropic Files
API. Keeping the staging in one place means the two engines can never disagree
about which files an analysis "saw".

Extracted from `chat_analysis._stage_workspace` verbatim (behaviour-preserving):
same 20 MB cap, same per-sheet xlsx→CSV conversion, same skip-don't-choke
handling of unreadable workbooks.
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

# Mirrors routes/datasets.py MAX_UPLOAD_BYTES — anything larger never landed via
# the upload route, so a bigger file in raw/ is unexpected; skip, don't choke.
MAX_FILE_BYTES = 20 * 1024 * 1024


def stage_workspace(raw_dir: Path, workdir: Path) -> list[str]:
    """Copy analyzable tabular files into the throwaway workdir.

    CSVs are copied as-is; each sheet of an .xlsx becomes its own CSV (the
    engine only globs *.csv). Returns the staged filenames; unconvertible or
    oversized files are skipped silently — partial coverage beats no answer,
    and the engine's representation manifest reports exactly what it saw.
    """
    staged: list[str] = []
    for src in sorted(raw_dir.iterdir()):
        if not src.is_file() or src.stat().st_size > MAX_FILE_BYTES:
            continue
        suffix = src.suffix.lower()
        if suffix == ".csv":
            shutil.copy(src, workdir / src.name)
            staged.append(src.name)
        elif suffix in (".xlsx", ".xls"):
            try:
                import pandas as pd

                sheets = pd.read_excel(src, sheet_name=None)
            except Exception:  # noqa: BLE001 — bad workbook ≠ failed analysis
                logger.warning("DS chat: could not read workbook %s", src.name, exc_info=True)
                continue
            for sheet_name, df in sheets.items():
                if df.empty:
                    continue
                out = workdir / f"{src.stem}_{sheet_name}.csv".replace(" ", "_")
                df.to_csv(out, index=False)
                staged.append(out.name)
    return staged
