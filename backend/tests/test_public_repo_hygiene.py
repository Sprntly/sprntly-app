"""Fail when a real customer name appears anywhere in the tracked repo.

`Sprntly/sprntly-app` is PUBLIC. Customer names, the names of individuals at
customers, and anything describing a specific customer's commercial
relationship with us are world-readable the moment they land on main. This
guard is the durable half of the 2026-08-08 scrub; the scrub itself was
one-time cleanup.

HOW IT WORKS. `fixtures/customer_name_denylist.txt` holds SHA-256 hashes of
normalised real names, never the names themselves — a plaintext list in a
public repo would re-publish exactly what we are removing.

WHAT HASHING BUYS, PRECISELY: grep-resistance, not confidentiality. Each entry
stores its normalised LENGTH so the hot loop can skip phrases of the wrong size
without hashing them, and that length also narrows a brute-force search. A
short name is therefore recoverable — a length-5 entry falls in about twenty
seconds of single-threaded CPython. Read "hashed, never plaintext" as "will not
turn up in a code search or a scraped dataset", which is the actual goal, and
see MIN_LENGTH / MAX_WEAK_ENTRIES below. The checker walks
every tracked text file, forms candidate word-phrases, normalises them the same
way, and hashes. Each hash is stored with its normalised LENGTH, so the hot loop
discards phrases of the wrong length without hashing them at all; the stored
word count only bounds how many adjacent words are ever worth joining.

WHAT IT CANNOT DO. It only sees the working tree. Every one of these strings is
still in git history, and no test can change that — see the note in
CONVENTIONS.md. It also cannot catch a customer described without being named
("the large insurer we onboarded in July"); that judgement stays with review.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

try:
    import pytest
except ModuleNotFoundError:  # pragma: no cover - the standalone CI path
    # `.github/workflows/repo-hygiene.yml` runs this module directly, with no
    # `pip install`, so that an unfiltered every-PR lane stays free. The only
    # pytest surface here is one fixture decorator, so a shim is enough — and
    # keeping the import optional is what lets the guard run in a bare
    # interpreter, a pre-commit hook, or anywhere else without a venv.
    class _PytestShim:
        @staticmethod
        def fixture(*_a, **_k):
            def decorate(fn):
                return fn
            return decorate

    pytest = _PytestShim()  # type: ignore[assignment]

REPO_ROOT = Path(__file__).resolve().parents[2]
DENYLIST = Path(__file__).parent / "fixtures" / "customer_name_denylist.txt"

_WORD = re.compile(r"[A-Za-z0-9]+")

#: Shortest normalised name accepted onto the list. Mirrors
#: `scripts/add_denylisted_name.py`, which is the only sanctioned way to add
#: one. Below this a name both collides with ordinary words and — because the
#: stored length narrows the search — stops being meaningfully concealed by
#: hashing at all.
MIN_LENGTH = 4

#: How many entries are short enough to be cheap to recover from their hash.
#: A RATCHET, not a target: MEASURED at 36 (one entry of length 5, eighteen of
#: length 6, seventeen of length 7). The length-5 one was recovered in ~20s of
#: single-threaded CPython over [a-z0-9]^5, so treat that name as still
#: exposed regardless of this list. Existing entries are grandfathered
#: because the names cannot be removed from a hashed list without knowing them,
#: but this number must never go UP — a new short entry publishes the name
#: about as effectively as leaving it in the code did.
MAX_WEAK_ENTRIES = 36

# Paths that still contain a denylisted name and are NOT fixed by this change.
#
# This list is EXPLICIT and exhaustive on purpose. Deriving it (e.g. from `git
# grep asurion`) would mean a newly-added file carrying the name is absorbed
# silently — the allowlist would grow itself and the guard would stop guarding.
# Every entry is an exact path, never a directory or glob, so a NEW file must
# fail even when it sits beside an allowed one.
#
# Two debts are recorded here, neither of which is a comment edit:
#
#  1. The `asurion` demo dataset. The name is the dataset SLUG — a live
#     `datasets` row, the default argument in a dozen call sites, a
#     deploy-workflow copy step, and an already-applied migration. Renaming it
#     is a data migration, not a scrub. Needs Apurva + sprntly-db-engineer.
#  2. `20260802160000_call_index.sql` — an ALREADY-APPLIED migration whose
#     header comment names two accounts. Editing an applied migration file is
#     how this repo has blocked every backend deploy twice before, so it is
#     handed to sprntly-db-engineer rather than fixed here.
KNOWN_UNFIXED: dict[Path, frozenset[str]] = {
    Path(".github/workflows/deploy-backend.yml"): frozenset({"bf0d1b7cf27b197fbad5e6b82996d8921c2b8a1cbdd3c8f2de4ac842f9784d10"}),
    Path("backend/README.md"): frozenset({"bf0d1b7cf27b197fbad5e6b82996d8921c2b8a1cbdd3c8f2de4ac842f9784d10"}),
    Path("backend/app/cli.py"): frozenset({"bf0d1b7cf27b197fbad5e6b82996d8921c2b8a1cbdd3c8f2de4ac842f9784d10"}),
    Path("backend/app/corpus.py"): frozenset({"bf0d1b7cf27b197fbad5e6b82996d8921c2b8a1cbdd3c8f2de4ac842f9784d10"}),
    Path("backend/app/datasets.py"): frozenset({"bf0d1b7cf27b197fbad5e6b82996d8921c2b8a1cbdd3c8f2de4ac842f9784d10"}),
    Path("backend/app/db/briefs.py"): frozenset({"bf0d1b7cf27b197fbad5e6b82996d8921c2b8a1cbdd3c8f2de4ac842f9784d10"}),
    Path("backend/app/db/datasets.py"): frozenset({"bf0d1b7cf27b197fbad5e6b82996d8921c2b8a1cbdd3c8f2de4ac842f9784d10"}),
    Path("backend/app/evidence_kg.py"): frozenset({"bf0d1b7cf27b197fbad5e6b82996d8921c2b8a1cbdd3c8f2de4ac842f9784d10"}),
    Path("backend/app/evidence_runner.py"): frozenset({"bf0d1b7cf27b197fbad5e6b82996d8921c2b8a1cbdd3c8f2de4ac842f9784d10"}),
    Path("backend/app/main.py"): frozenset({"bf0d1b7cf27b197fbad5e6b82996d8921c2b8a1cbdd3c8f2de4ac842f9784d10"}),
    Path("backend/app/prd_runner.py"): frozenset({"bf0d1b7cf27b197fbad5e6b82996d8921c2b8a1cbdd3c8f2de4ac842f9784d10"}),
    Path("backend/app/prompts.py"): frozenset({"bf0d1b7cf27b197fbad5e6b82996d8921c2b8a1cbdd3c8f2de4ac842f9784d10"}),
    Path("backend/data/README.md"): frozenset({"bf0d1b7cf27b197fbad5e6b82996d8921c2b8a1cbdd3c8f2de4ac842f9784d10"}),
    Path("backend/data/asurion/_reference/asurion_expected_output.md"): frozenset({"bf0d1b7cf27b197fbad5e6b82996d8921c2b8a1cbdd3c8f2de4ac842f9784d10"}),
    Path("backend/data/asurion/asurion_analytics.md"): frozenset({"bf0d1b7cf27b197fbad5e6b82996d8921c2b8a1cbdd3c8f2de4ac842f9784d10"}),
    Path("backend/data/asurion/asurion_business_context.md"): frozenset({"bf0d1b7cf27b197fbad5e6b82996d8921c2b8a1cbdd3c8f2de4ac842f9784d10"}),
    Path("backend/data/asurion/asurion_qualitative_signals.md"): frozenset({"bf0d1b7cf27b197fbad5e6b82996d8921c2b8a1cbdd3c8f2de4ac842f9784d10"}),
    Path("backend/data/sprntly_evidence_sample.md"): frozenset({"bf0d1b7cf27b197fbad5e6b82996d8921c2b8a1cbdd3c8f2de4ac842f9784d10"}),
    Path("backend/data/sprntly_prd_sample.md"): frozenset({"bf0d1b7cf27b197fbad5e6b82996d8921c2b8a1cbdd3c8f2de4ac842f9784d10"}),
    Path("backend/tests/test_ask_document_retrieval.py"): frozenset({"bf0d1b7cf27b197fbad5e6b82996d8921c2b8a1cbdd3c8f2de4ac842f9784d10"}),
    Path("backend/tests/test_ask_runner.py"): frozenset({"bf0d1b7cf27b197fbad5e6b82996d8921c2b8a1cbdd3c8f2de4ac842f9784d10"}),
    Path("backend/tests/test_chat_kg_retrieval.py"): frozenset({"bf0d1b7cf27b197fbad5e6b82996d8921c2b8a1cbdd3c8f2de4ac842f9784d10"}),
    Path("backend/tests/test_corpus.py"): frozenset({"bf0d1b7cf27b197fbad5e6b82996d8921c2b8a1cbdd3c8f2de4ac842f9784d10"}),
    Path("backend/tests/test_cross_connector_sweep.py"): frozenset({"bf0d1b7cf27b197fbad5e6b82996d8921c2b8a1cbdd3c8f2de4ac842f9784d10"}),
    Path("backend/tests/test_datasets_service.py"): frozenset({"bf0d1b7cf27b197fbad5e6b82996d8921c2b8a1cbdd3c8f2de4ac842f9784d10"}),
    Path("backend/tests/test_document_catalog_backfill.py"): frozenset({"bf0d1b7cf27b197fbad5e6b82996d8921c2b8a1cbdd3c8f2de4ac842f9784d10"}),
    Path("backend/tests/test_evidence_runner.py"): frozenset({"bf0d1b7cf27b197fbad5e6b82996d8921c2b8a1cbdd3c8f2de4ac842f9784d10"}),
    Path("backend/tests/test_prd_kg.py"): frozenset({"bf0d1b7cf27b197fbad5e6b82996d8921c2b8a1cbdd3c8f2de4ac842f9784d10"}),
    Path("backend/tests/test_prd_runner.py"): frozenset({"bf0d1b7cf27b197fbad5e6b82996d8921c2b8a1cbdd3c8f2de4ac842f9784d10"}),
    Path("backend/tests/test_prompts.py"): frozenset({"bf0d1b7cf27b197fbad5e6b82996d8921c2b8a1cbdd3c8f2de4ac842f9784d10"}),
    Path("scripts/convert_dataset.py"): frozenset({"bf0d1b7cf27b197fbad5e6b82996d8921c2b8a1cbdd3c8f2de4ac842f9784d10"}),
    Path("supabase/migrations/20260525120400_datasets.sql"): frozenset({"bf0d1b7cf27b197fbad5e6b82996d8921c2b8a1cbdd3c8f2de4ac842f9784d10"}),
    Path("supabase/migrations/20260802160000_call_index.sql"): frozenset({"4fb633e10caac3fb8759559e5d77b4617588f7c938921f928249c5c1209be2ee", "7c712593e520b3452b3053404605d63a6fbb5a953092f1ffc36a66a3448b03d8"}),
    Path("web/app/components/design-agent/__tests__/ShareMenu.dom.test.tsx"): frozenset({"bf0d1b7cf27b197fbad5e6b82996d8921c2b8a1cbdd3c8f2de4ac842f9784d10"}),
    Path("web/app/components/shared/__tests__/BriefChat.generate-cta-gating.dom.test.tsx"): frozenset({"bf0d1b7cf27b197fbad5e6b82996d8921c2b8a1cbdd3c8f2de4ac842f9784d10"}),
    Path("web/app/components/shared/__tests__/BriefChat.generating.dom.test.tsx"): frozenset({"bf0d1b7cf27b197fbad5e6b82996d8921c2b8a1cbdd3c8f2de4ac842f9784d10"}),
    Path("web/app/components/shared/__tests__/BriefChat.initial-load.dom.test.tsx"): frozenset({"bf0d1b7cf27b197fbad5e6b82996d8921c2b8a1cbdd3c8f2de4ac842f9784d10"}),
    Path("web/app/components/shared/__tests__/BriefChat.no-connector.dom.test.tsx"): frozenset({"bf0d1b7cf27b197fbad5e6b82996d8921c2b8a1cbdd3c8f2de4ac842f9784d10"}),
    Path("web/app/lib/__tests__/brief-v2-adapter.test.ts"): frozenset({"bf0d1b7cf27b197fbad5e6b82996d8921c2b8a1cbdd3c8f2de4ac842f9784d10"}),
    Path("web/app/lib/__tests__/sourcesApi.test.ts"): frozenset({"bf0d1b7cf27b197fbad5e6b82996d8921c2b8a1cbdd3c8f2de4ac842f9784d10"}),
    Path("web/app/lib/__tests__/useActiveCompany.test.ts"): frozenset({"bf0d1b7cf27b197fbad5e6b82996d8921c2b8a1cbdd3c8f2de4ac842f9784d10"}),
    Path("web/app/lib/api.ts"): frozenset({"bf0d1b7cf27b197fbad5e6b82996d8921c2b8a1cbdd3c8f2de4ac842f9784d10"}),
    Path("web/app/lib/useActiveCompany.ts"): frozenset({"bf0d1b7cf27b197fbad5e6b82996d8921c2b8a1cbdd3c8f2de4ac842f9784d10"}),
    Path("web/app/lib/useBriefHydration.ts"): frozenset({"bf0d1b7cf27b197fbad5e6b82996d8921c2b8a1cbdd3c8f2de4ac842f9784d10"}),
}


def _load_denylist() -> tuple[dict[int, set[str]], int]:
    """{normalised_length: {hash, ...}}, plus how many words to join at most.

    Buckets are keyed on LENGTH ALONE, never on the stored word count. Because
    normalisation strips spaces, 'Vandelay Industries' and 'vandelayindustries'
    are the same 18-character string, so a two-word denylist entry must still be
    caught when someone writes it as one token. Keying on word count would put
    those two spellings in different buckets and silently miss the second — the
    stored count only tells the scanner how many adjacent words are ever worth
    joining.
    """
    buckets: dict[int, set[str]] = {}
    max_words = 0
    for line in DENYLIST.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        wc_s, len_s, digest = line.split(":")
        buckets.setdefault(int(len_s), set()).add(digest)
        max_words = max(max_words, int(wc_s))
    return buckets, max_words


def _tracked_text_files() -> list[Path]:
    out = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "grep", "-I", "--name-only", "-e", ""],
        capture_output=True, text=True, check=False,
    )
    return [Path(p) for p in out.stdout.split("\n") if p]


def _hits_in(text: str, buckets, max_words) -> set[str]:
    """Normalised phrases in `text` whose hash is denylisted."""
    found: set[str] = set()
    tokens = [t.lower() for t in _WORD.findall(text)]
    lengths = [len(t) for t in tokens]
    n = len(tokens)
    for start in range(n):
        total = 0
        for count in range(1, max_words + 1):
            end = start + count
            if end > n:
                break
            total += lengths[end - 1]
            bucket = buckets.get(total)
            if not bucket:
                continue  # no denylisted name has this length - never hashed
            phrase = "".join(tokens[start:end])
            if hashlib.sha256(phrase.encode()).hexdigest() in bucket:
                found.add(phrase)
    return found


@pytest.fixture(scope="module")
def denylist():
    buckets, max_words = _load_denylist()
    assert buckets, "denylist is empty - the guard would pass on anything"
    return buckets, max_words


def test_denylist_is_hashed_not_plaintext():
    """The denylist must not itself leak the names it protects."""
    for line in DENYLIST.read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            wc, length, digest = line.split(":")
            assert wc.isdigit() and length.isdigit()
            assert re.fullmatch(r"[0-9a-f]{64}", digest), (
                f"denylist entry is not a sha256 hash: {line!r}"
            )


def test_the_guard_fires_on_a_known_bad_string(denylist):
    """A guard is not trusted until it has been shown to fail on known-bad input.

    'Canarycorp Sentinel' is a CANARY: a fictional name that is on the denylist
    for no reason other than to be caught here. Using a real removed name would
    mean this guard permanently kept one of the very strings it exists to
    delete. All spellings must be caught — the normalisation is what makes the
    guard robust to someone writing a name a different way.
    """
    buckets, max_words = denylist
    for spelling in (
        "Canarycorp Sentinel", "canarycorpsentinel",
        "Canarycorp-Sentinel", "CANARYCORP  SENTINEL",
    ):
        assert _hits_in(f"# measured on {spelling} last week", buckets, max_words), (
            f"guard failed to catch {spelling!r}"
        )


def test_the_guard_is_quiet_on_synthetic_names(denylist):
    """The fictional set the scrub substituted must NOT trip the guard."""
    buckets, max_words = denylist
    clean = (
        "Measured on Northwind. summarize the Vandelay Industries call, "
        "the Initech one, Globex, Contoso, Hooli, Cyberdyne, Tyrell, Acme."
    )
    assert _hits_in(clean, buckets, max_words) == set()


def test_no_real_customer_name_in_the_tracked_repo(denylist):
    buckets, max_words = denylist
    files = _tracked_text_files()

    # Fail LOUD rather than pass silently when the scan has no input. Without
    # this the test is green in any checkout where `git grep` returns nothing —
    # no git, a sparse checkout, a wrong REPO_ROOT — which is the worst outcome
    # for a guard: it converts "nobody checked" into "someone checked, it's
    # fine". 500 is far below the ~1,960 tracked text files today and far above
    # anything a broken invocation would produce.
    assert len(files) > 500, (
        f"only {len(files)} tracked text files found under {REPO_ROOT} — the "
        "scan has no real input, so a pass here would be meaningless"
    )

    offenders: dict[str, set[str]] = {}
    unreadable: list[str] = []
    for rel in files:
        if rel == Path("backend/tests/test_public_repo_hygiene.py"):
            # Skipped only because it spells the CANARY out to prove the guard
            # fires. No real customer name lives here, so nothing is hidden.
            continue
        try:
            text = (REPO_ROOT / rel).read_text(errors="ignore")
        except OSError as exc:
            # NOT `continue`. `errors="ignore"` makes UnicodeDecodeError
            # unreachable, so the old except-and-skip meant "any file we cannot
            # open is silently clean" — permission denied, a broken symlink, a
            # tracked symlink resolving outside the tree. That is the last
            # fail-open path, and a guard that skips what it cannot read is a
            # guard you cannot trust the pass of.
            unreadable.append(f"{rel}: {exc}")
            continue

        # THE PATH IS SCANNED TOO. `_hits_in` only ever saw file CONTENT, so a
        # new file at `backend/data/<realname>/notes.md` with clean contents was
        # invisible — directly contradicting this module's claim that a new file
        # must fail even when it sits beside an allowed one.
        hits = _hits_in(text, buckets, max_words) | _hits_in(
            str(rel).replace("/", " ").replace("_", " "), buckets, max_words
        )
        if not hits:
            continue

        # PER-NAME, not per-file. `KNOWN_UNFIXED` used to mute a whole file, so
        # a brand-new, unrelated name landing in one of the 44 listed paths —
        # among them the two most prompt-heavy backend modules and the web api
        # client — was never looked at. The docstring's own argument against a
        # self-growing allowlist applies just as much to a new NAME in an
        # already-listed file.
        allowed = KNOWN_UNFIXED.get(rel, frozenset())
        unexpected = {
            h for h in hits
            if hashlib.sha256(h.encode()).hexdigest() not in allowed
        }
        if unexpected:
            offenders[str(rel)] = unexpected

    assert not unreadable, (
        "tracked files could not be read, so the scan cannot claim to be "
        "complete:\n  " + "\n  ".join(unreadable)
    )

    assert not offenders, (
        "Real customer names found in a PUBLIC repo:\n"
        + "\n".join(f"  {path}: {sorted(names)}" for path, names in sorted(offenders.items()))
        + "\n\nReplace with a synthetic name (see CONVENTIONS.md, "
        "'Public-repo hygiene'). Do not add the file to KNOWN_UNFIXED to "
        "silence this."
    )


def test_known_unfixed_has_no_stale_entries(denylist):
    """Every carve-out must still be needed.

    An entry whose file is gone, or whose name has since been scrubbed, is a
    mute with nothing behind it — harmless today, and exactly how the list
    quietly grows into a blanket suppression nobody audits. Nothing enforced
    this before, so it would have rotted silently.
    """
    buckets, max_words = denylist
    stale: list[str] = []
    for rel, allowed in KNOWN_UNFIXED.items():
        path = REPO_ROOT / rel
        if not path.is_file():
            stale.append(f"{rel}: file no longer exists")
            continue
        hits = _hits_in(path.read_text(errors="ignore"), buckets, max_words)
        present = {hashlib.sha256(h.encode()).hexdigest() for h in hits}
        gone = allowed - present
        if gone:
            stale.append(
                f"{rel}: {len(gone)} allowed digest(s) no longer appear — the "
                f"name was scrubbed, so drop the carve-out"
            )
    assert not stale, "KNOWN_UNFIXED has stale entries:\n  " + "\n  ".join(stale)


def test_denylist_entries_are_well_formed_and_safe(denylist):
    """The format check alone let a catastrophic entry through.

    Verified: a hand-added `1:1:<sha256("a")>` satisfies "three fields, first
    two numeric, third 64 hex" — and then matches the token `a` in ordinary
    prose, so EVERY tracked file becomes an offender at once. Because the list
    is hashed, nobody can tell which entry did it.

    A short entry is also the one that is not really confidential: a length-5
    name is brute-forceable over `[a-z0-9]^5` in about twenty seconds of
    single-threaded CPython, because the stored length narrows the search. The
    floor here is why the fixture header promises grep-resistance rather than
    secrecy.
    """
    seen: dict[str, int] = {}
    problems: list[str] = []
    for lineno, line in enumerate(DENYLIST.read_text().splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            continue
        parts = line.split(":")
        if len(parts) != 3:
            problems.append(f"line {lineno}: expected 3 fields, got {len(parts)}")
            continue
        wc_s, len_s, digest = parts
        if not (wc_s.isdigit() and len_s.isdigit()):
            problems.append(f"line {lineno}: non-numeric word count or length")
            continue
        wc, length = int(wc_s), int(len_s)
        if length < MIN_LENGTH:
            problems.append(
                f"line {lineno}: normalised length {length} is below MIN_LENGTH "
                f"{MIN_LENGTH} — short entries match ordinary words AND are "
                f"cheap to brute-force from the stored length"
            )
        if not 1 <= wc <= length:
            problems.append(f"line {lineno}: word count {wc} impossible for length {length}")
        if len(digest) != 64 or not all(c in "0123456789abcdef" for c in digest):
            problems.append(f"line {lineno}: digest is not 64 lowercase hex chars")
        if digest in seen:
            problems.append(f"line {lineno}: duplicate of line {seen[digest]}")
        seen[digest] = lineno
    assert not problems, "denylist is malformed:\n  " + "\n  ".join(problems)


def test_short_denylist_entries_do_not_increase():
    """A ratchet on the entries that hashing does not really conceal.

    The stored length narrows a brute-force search, so a short name on this
    list is recoverable in seconds. The existing ones are grandfathered — they
    cannot be removed from a hashed list without knowing what they are — but
    adding another would be publishing a name while believing the opposite.
    Use a longer distinctive phrase (full company name, not an abbreviation).
    """
    weak = 0
    for line in DENYLIST.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        _, len_s, _ = line.split(":")
        if int(len_s) < 8:
            weak += 1
    assert weak <= MAX_WEAK_ENTRIES, (
        f"{weak} denylist entries are shorter than 8 normalised characters, up "
        f"from {MAX_WEAK_ENTRIES}. A short entry is cheap to recover from its "
        f"hash plus the stored length, so listing one publishes the name it "
        f"was meant to hide. Prefer a longer distinctive phrase."
    )


# ── Runnable without pytest ──────────────────────────────────────────────────
# The CI lane that matters (.github/workflows/repo-hygiene.yml) has no path
# filter, so it runs on EVERY push and PR. That is only affordable if it
# installs nothing, and `setup-python` gives a clean interpreter with no pytest
# in it. So this module is executable directly:
#
#     python backend/tests/test_public_repo_hygiene.py
#
# It calls the same functions pytest does — no second implementation to drift —
# and exits non-zero on the first failure. Also handy locally and in a
# pre-commit hook, where nobody wants to activate a venv to check a name.
if __name__ == "__main__":
    import sys as _sys
    import traceback as _tb

    _fixture = _load_denylist()
    _checks = [
        (test_denylist_is_hashed_not_plaintext, ()),
        (test_denylist_entries_are_well_formed_and_safe, (_fixture,)),
        (test_short_denylist_entries_do_not_increase, ()),
        (test_the_guard_fires_on_a_known_bad_string, (_fixture,)),
        (test_the_guard_is_quiet_on_synthetic_names, (_fixture,)),
        (test_known_unfixed_has_no_stale_entries, (_fixture,)),
        (test_no_real_customer_name_in_the_tracked_repo, (_fixture,)),
    ]
    _failed = 0
    for _fn, _args in _checks:
        try:
            _fn(*_args)
            print(f"ok    {_fn.__name__}")
        except AssertionError:
            _failed += 1
            print(f"FAIL  {_fn.__name__}\n{_tb.format_exc()}")
    print(f"\n{len(_checks) - _failed} passed, {_failed} failed")
    _sys.exit(1 if _failed else 0)
