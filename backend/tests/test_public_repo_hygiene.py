"""Fail when a real customer name appears anywhere in the tracked repo.

`Sprntly/sprntly-app` is PUBLIC. Customer names, the names of individuals at
customers, and anything describing a specific customer's commercial
relationship with us are world-readable the moment they land on main. This
guard is the durable half of the 2026-08-08 scrub; the scrub itself was
one-time cleanup.

HOW IT WORKS. `fixtures/customer_name_denylist.txt` holds SHA-256 hashes of
normalised real names, never the names themselves — a plaintext list in a
public repo would re-publish exactly what we are removing. The checker walks
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

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DENYLIST = Path(__file__).parent / "fixtures" / "customer_name_denylist.txt"

_WORD = re.compile(r"[A-Za-z0-9]+")

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
KNOWN_UNFIXED = frozenset(Path(p) for p in (
    # 2. applied migration - comment only, owned by sprntly-db-engineer
    "supabase/migrations/20260802160000_call_index.sql",
    # 1. the asurion demo dataset and its call sites
    ".github/workflows/deploy-backend.yml",
    "backend/README.md",
    "backend/app/cli.py",
    "backend/app/corpus.py",
    "backend/app/datasets.py",
    "backend/app/db/briefs.py",
    "backend/app/db/datasets.py",
    "backend/app/evidence_kg.py",
    "backend/app/evidence_runner.py",
    "backend/app/main.py",
    "backend/app/prd_runner.py",
    "backend/app/prompts.py",
    "backend/data/README.md",
    "backend/data/asurion/_reference/asurion_expected_output.md",
    "backend/data/asurion/asurion_analytics.md",
    "backend/data/asurion/asurion_business_context.md",
    "backend/data/asurion/asurion_qualitative_signals.md",
    "backend/data/sprntly_evidence_sample.md",
    "backend/data/sprntly_prd_sample.md",
    "backend/tests/test_ask_document_retrieval.py",
    "backend/tests/test_ask_runner.py",
    "backend/tests/test_chat_kg_retrieval.py",
    "backend/tests/test_corpus.py",
    "backend/tests/test_cross_connector_sweep.py",
    "backend/tests/test_datasets_service.py",
    "backend/tests/test_document_catalog_backfill.py",
    "backend/tests/test_evidence_runner.py",
    "backend/tests/test_prd_kg.py",
    "backend/tests/test_prd_runner.py",
    "backend/tests/test_prompts.py",
    "scripts/convert_dataset.py",
    "supabase/migrations/20260525120400_datasets.sql",
    "web/app/components/design-agent/__tests__/ShareMenu.dom.test.tsx",
    "web/app/components/shared/__tests__/BriefChat.generate-cta-gating.dom.test.tsx",
    "web/app/components/shared/__tests__/BriefChat.generating.dom.test.tsx",
    "web/app/components/shared/__tests__/BriefChat.initial-load.dom.test.tsx",
    "web/app/components/shared/__tests__/BriefChat.no-connector.dom.test.tsx",
    "web/app/lib/__tests__/brief-v2-adapter.test.ts",
    "web/app/lib/__tests__/sourcesApi.test.ts",
    "web/app/lib/__tests__/useActiveCompany.test.ts",
    "web/app/lib/api.ts",
    "web/app/lib/useActiveCompany.ts",
    "web/app/lib/useBriefHydration.ts",
))


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
    for rel in files:
        if rel == Path("backend/tests/test_public_repo_hygiene.py"):
            # Skipped only because it spells the CANARY out to prove the guard
            # fires. No real customer name lives here, so nothing is hidden.
            continue
        if rel in KNOWN_UNFIXED:
            continue
        try:
            text = (REPO_ROOT / rel).read_text(errors="ignore")
        except (OSError, UnicodeDecodeError):
            continue
        hits = _hits_in(text, buckets, max_words)
        if hits:
            offenders[str(rel)] = hits

    assert not offenders, (
        "Real customer names found in a PUBLIC repo:\n"
        + "\n".join(f"  {path}: {sorted(names)}" for path, names in sorted(offenders.items()))
        + "\n\nReplace with a synthetic name (see CONVENTIONS.md, "
        "'Public-repo hygiene'). Do not add the file to KNOWN_UNFIXED to "
        "silence this."
    )
