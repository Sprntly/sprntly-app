"""Dataset onboarding service.

A "dataset" maps 1:1 to a company (or product, or whatever the user is
modeling). Files live under DATA_DIR/<slug>/, where:

  DATA_DIR/<slug>/
    raw/        ← uploaded originals (.docx, .xlsx, .pdf, .txt)
    *.md        ← converted corpus, fed to the LLM
    _reference/ ← optional answer keys (ignored by the corpus loader)

The DB `datasets` table is the source of truth for which slugs exist; the
filesystem holds the actual content. Both are kept in sync here — create
inserts a row + mkdir, upload writes the file + converts it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import logging

from app import db
from app.config import settings
from app.ingest import UnsupportedFileType, convert, md_filename

logger = logging.getLogger(__name__)


_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,62}$")


class DatasetError(ValueError):
    """Base for dataset onboarding problems that should surface as 4xx."""


class InvalidSlug(DatasetError):
    pass


class DatasetAlreadyExists(DatasetError):
    pass


class DatasetNotFound(DatasetError):
    pass


@dataclass(frozen=True)
class IngestedFile:
    """Result of one upload — what the frontend renders in the wizard."""
    original_filename: str
    stored_raw_path: str
    md_path: str
    md_chars: int


def validate_slug(slug: str) -> str:
    slug = (slug or "").strip().lower()
    if not _SLUG_RE.match(slug):
        raise InvalidSlug(
            "Slug must be 2-63 chars, start with a letter or digit, and contain "
            "only lowercase letters, digits, hyphens, or underscores."
        )
    return slug


def dataset_path(slug: str) -> Path:
    return settings.data_path / slug


def raw_path(slug: str) -> Path:
    return dataset_path(slug) / "raw"


def create_dataset(slug: str, display_name: str) -> dict:
    """Register a dataset and mkdir its folders. Idempotent on the directory side
    but raises DatasetAlreadyExists if the DB already has it — callers should
    catch that to render a clear 'pick a different name' error.
    """
    slug = validate_slug(slug)
    display_name = (display_name or "").strip()
    if not display_name:
        raise DatasetError("display_name is required")
    if db.dataset_exists(slug):
        raise DatasetAlreadyExists(f"Dataset {slug!r} already exists")

    base = dataset_path(slug)
    (base / "raw").mkdir(parents=True, exist_ok=True)
    db.insert_dataset(slug=slug, display_name=display_name)

    # Seed default enterprise input sources for this dataset.
    try:
        db.upsert_input_source(slug, "csv_upload", enabled=True)
        db.upsert_input_source(slug, "google_drive", enabled=False)
    except Exception:
        logger.warning("Failed to seed input sources for %s (table may not exist yet)", slug, exc_info=True)

    return {
        "slug": slug,
        "display_name": display_name,
        "data_dir": str(base),
    }


def _unique_raw_name(slug: str, original_filename: str) -> Path:
    """Pick a non-colliding path under raw/ for the original upload."""
    raw = raw_path(slug)
    raw.mkdir(parents=True, exist_ok=True)
    candidate = raw / Path(original_filename).name
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    n = 1
    while True:
        alt = raw / f"{stem}.{n}{suffix}"
        if not alt.exists():
            return alt
        n += 1


def ingest_file(slug: str, filename: str, data: bytes) -> IngestedFile:
    """Save the raw bytes, convert to markdown, write the .md sibling."""
    if not db.dataset_exists(slug):
        raise DatasetNotFound(f"Dataset {slug!r} does not exist")
    raw_target = _unique_raw_name(slug, filename)
    raw_target.write_bytes(data)
    try:
        md_text = convert(filename, data)
    except UnsupportedFileType:
        # Roll back the raw write so the dataset doesn't end up with an
        # orphan we can't convert — the user can re-upload after fixing.
        raw_target.unlink(missing_ok=True)
        raise

    md_target = dataset_path(slug) / md_filename(filename)
    # Collision under the converted-md name: append .1, .2, ... like raw/.
    if md_target.exists():
        stem = md_target.stem
        n = 1
        while True:
            alt = md_target.with_name(f"{stem}.{n}.md")
            if not alt.exists():
                md_target = alt
                break
            n += 1
    md_target.write_text(md_text)
    return IngestedFile(
        original_filename=Path(filename).name,
        stored_raw_path=str(raw_target),
        md_path=str(md_target),
        md_chars=len(md_text),
    )


def list_datasets() -> list[dict]:
    """All datasets with summary info for the picker UI."""
    out: list[dict] = []
    for row in db.list_datasets():
        slug = row["slug"]
        brief = db.get_current_brief(slug)
        out.append({
            **row,
            "has_brief": brief is not None,
            "brief_id": brief["id"] if brief else None,
            "raw_file_count": _count_raw_files(slug),
            "md_file_count": _count_md_files(slug),
        })
    return out


def _count_raw_files(slug: str) -> int:
    raw = raw_path(slug)
    if not raw.exists():
        return 0
    return sum(1 for p in raw.iterdir() if p.is_file())


def _count_md_files(slug: str) -> int:
    base = dataset_path(slug)
    if not base.exists():
        return 0
    return sum(1 for p in base.glob("*.md") if not p.name.startswith("_"))


# ----- Startup seeding ---------------------------------------------------

def seed_filesystem_datasets() -> int:
    """For every directory under DATA_DIR that looks like a dataset (has at
    least one .md not starting with _), register it in the `datasets` table if
    it isn't already. Returns the count of newly registered slugs.

    Runs on app startup so the existing `asurion` corpus on EC2 (and any
    sibling dirs added manually) get a row without manual SQL.
    """
    base = settings.data_path
    if not base.exists():
        return 0
    seeded = 0  # noqa: SIM113
    for child in sorted(base.iterdir()):
        if not child.is_dir():
            continue
        # A dataset has at least one corpus .md
        has_md = any(p.is_file() and not p.name.startswith("_") for p in child.glob("*.md"))
        if not has_md:
            continue
        if db.dataset_exists(child.name):
            continue
        # Title-case the slug as a reasonable default display name.
        display = child.name.replace("_", " ").replace("-", " ").title()
        db.insert_dataset(slug=child.name, display_name=display)
        seeded += 1
    return seeded


# ----- Onboarding context seeding ------------------------------------------


def seed_onboarding_context(
    slug: str,
    *,
    company_name: str = "",
    product_name: str = "",
    industry: str = "",
    kpi_tree: dict | None = None,
    strategic_context: str = "",
) -> str:
    """Write onboarding metadata into the corpus as a markdown file.

    This ensures brief generation has company context even before any
    connector data arrives.  The file is named ``onboarding_context.md``
    (no underscore prefix, so the corpus loader picks it up).

    Returns the path of the written file.
    """
    if not db.dataset_exists(slug):
        raise DatasetNotFound(f"Dataset {slug!r} does not exist")

    lines: list[str] = ["# Company & Product Context\n"]
    if company_name:
        lines.append(f"**Company:** {company_name}")
    if product_name:
        lines.append(f"**Product:** {product_name}")
    if industry:
        lines.append(f"**Industry:** {industry}")
    if kpi_tree:
        lines.append("\n## KPIs")

        def _fmt(metric: str, description: str) -> str:
            metric = (metric or "").strip()
            description = (description or "").strip()
            return f"{metric} — {description}" if description else metric

        # The north star is stored as a {metric, description} object; tolerate
        # the legacy bare-string shape too.
        north_star = kpi_tree.get("north_star")
        if isinstance(north_star, dict):
            ns_text = _fmt(north_star.get("metric", ""), north_star.get("description", ""))
        else:
            ns_text = str(north_star or "").strip()
        if ns_text:
            lines.append(f"**North Star:** {ns_text}")

        # Supporting metrics live under primary_metrics + secondary_signals;
        # each is a {metric, description} object.
        supporting = list(kpi_tree.get("primary_metrics") or []) + list(
            kpi_tree.get("secondary_signals") or []
        )
        for metric in supporting:
            if not isinstance(metric, dict):
                continue
            text = _fmt(metric.get("metric", ""), metric.get("description", ""))
            if text:
                lines.append(f"- {text}")
    if strategic_context:
        lines.append(f"\n## Strategic Context\n\n{strategic_context}")

    md_text = "\n".join(lines) + "\n"
    target = dataset_path(slug) / "onboarding_context.md"
    target.write_text(md_text, encoding="utf-8")
    logger.info("Seeded onboarding context for %s (%d chars)", slug, len(md_text))
    return str(target)
