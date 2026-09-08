"""Opening an uploaded ZIP without trusting a byte of it.

An archive from a customer is hostile input in three specific ways, and every
one of them is a real technique rather than a hypothetical:

  * PATH TRAVERSAL — a member named ``../../etc/passwd`` or ``/etc/passwd``
    writes outside wherever you thought you were writing. Nothing here ever
    touches the filesystem, but a traversal name reaching a document title or a
    storage key is the same bug one layer along, so those members are dropped
    rather than sanitised.
  * ZIP BOMBS — a few KB that expand to gigabytes. The declared ``file_size``
    is attacker-controlled, so it is used only as an early tripwire; the actual
    read is capped independently, which is what makes a lying header harmless.
  * SHEER COUNT — ten thousand one-byte members is not a bomb by size, and
    still ruins whatever loops over them.

WHY THIS MODULE EXISTS. These guards were written twice already — in
``app/datasets.py`` for dataset archives and in ``app/skills/custom.py`` for
skill bundles — and a third upload surface (project documents) needed them
again. Three copies of a security check is three places for one of them to
drift, and the one that drifts is the one nobody re-reads. This is the shared
home; the two older copies predate it and converging them is a separate,
mechanical change worth doing on its own rather than smuggled into a feature.

Returns BYTES, deliberately. The skills path decodes members as UTF-8 markdown
because that is what a skill is; a document archive may hold PDFs and DOCX,
which are not text and must reach the converter as the bytes they are.
"""
from __future__ import annotations

import io
import zipfile
from pathlib import PurePosixPath

__all__ = [
    "ZipTooLarge",
    "NotAZip",
    "MAX_MEMBERS",
    "MAX_TOTAL_UNCOMPRESSED",
    "MAX_MEMBER_BYTES",
    "is_junk",
    "read_members",
]


class NotAZip(Exception):
    """The bytes are not a readable archive."""


class ZipTooLarge(Exception):
    """The archive is refused on size or count, before anything is read."""


# Sized to match `app/skills/custom.py`'s existing caps, which in turn mirror
# `app/datasets.py`. Kept identical rather than re-derived: three surfaces
# disagreeing about what "too big" means is how one of them becomes the soft
# target.
MAX_MEMBERS = 200
MAX_TOTAL_UNCOMPRESSED = 100 * 1024 * 1024  # 100 MB across the archive
MAX_MEMBER_BYTES = 20 * 1024 * 1024         # per extracted file


def is_junk(name: str) -> bool:
    """macOS resource-fork / metadata entries to ignore.

    A zip made on a Mac carries a `__MACOSX/` shadow of every real file. Left
    in, a five-document archive imports as ten, half of them unreadable — which
    reads to the person who uploaded it as the product being broken.
    """
    base = PurePosixPath(name).name
    return (
        name.startswith("__MACOSX/")
        or base.startswith("._")
        or base == ".DS_Store"
        or not base
    )


def read_members(
    data: bytes,
    *,
    max_members: int = MAX_MEMBERS,
    max_total_uncompressed: int = MAX_TOTAL_UNCOMPRESSED,
    max_member_bytes: int = MAX_MEMBER_BYTES,
) -> list[tuple[str, bytes]]:
    """Every readable member as ``(name, bytes)``, safety-checked.

    Names come back as the archive's own relative paths with any single
    top-level wrapper folder removed — ``docs.zip → docs/brief.md`` is what a
    person means by "the file called brief.md", not "docs/brief.md".

    Raises `NotAZip` for unreadable bytes and `ZipTooLarge` when the archive
    exceeds a cap. Directories, macOS junk and hostile paths are skipped
    silently: they are not errors a person can act on, and refusing the whole
    upload because a Mac added a `__MACOSX` folder would be absurd.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, OSError) as exc:
        raise NotAZip(str(exc)) from exc

    out: list[tuple[str, bytes]] = []
    declared_total = 0
    read_total = 0

    with zf:
        infos = [i for i in zf.infolist() if not i.is_dir() and not is_junk(i.filename)]

        # Names first, so the wrapper unwrap can look at the whole set before
        # anything is read.
        paths: list[tuple[PurePosixPath, zipfile.ZipInfo]] = []
        for info in infos:
            path = PurePosixPath(info.filename.replace("\\", "/"))
            if path.is_absolute() or ".." in path.parts:
                continue  # hostile path — skipped, never read
            if len(paths) >= max_members:
                raise ZipTooLarge("The archive contains too many files.")
            # The DECLARED size is attacker-controlled; this is an early
            # tripwire, not the enforcement — see the capped read below.
            declared_total += info.file_size
            if declared_total > max_total_uncompressed:
                raise ZipTooLarge("The archive is too large when uncompressed.")
            paths.append((path, info))

        # `docs.zip → docs/a.md, docs/b.md` unwraps to `a.md, b.md`. Only when
        # EVERY member shares one root, or the folder is meaningful structure
        # rather than packaging.
        roots = {p.parts[0] for p, _ in paths if p.parts}
        if len(roots) == 1 and paths and all(len(p.parts) > 1 for p, _ in paths):
            paths = [(PurePosixPath(*p.parts[1:]), info) for p, info in paths]

        for path, info in paths:
            with zf.open(info) as fh:
                # Read one byte past the cap: that is how a lying `file_size`
                # is caught, since the header said this would fit.
                raw = fh.read(max_member_bytes + 1)
            if len(raw) > max_member_bytes:
                raise ZipTooLarge(f"{path.name!r} in the archive is too large.")
            read_total += len(raw)
            if read_total > max_total_uncompressed:
                raise ZipTooLarge("The archive is too large when uncompressed.")
            out.append((str(path), raw))

    return out
