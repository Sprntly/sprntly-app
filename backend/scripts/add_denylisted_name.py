#!/usr/bin/env python3
"""Add a real customer/individual name to the hashed public-repo denylist.

    python backend/scripts/add_denylisted_name.py 'Some Corp' 'Jane Doe'

The name is normalised (lowercase, non-alphanumerics stripped) and stored as a
SHA-256 hash. It is never written to disk in plaintext — this repo is public, so
a readable list of customer names would re-publish exactly what the guard in
`backend/tests/test_public_repo_hygiene.py` exists to keep out.

Hashes are not secret-strength: someone who already suspects a name can confirm
it. They stop casual grep-and-scrape, which is the realistic risk here.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path

DENYLIST = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "customer_name_denylist.txt"

# Raised from 4 to match the guard's MAX_WEAK_ENTRIES ratchet, which sits at its
# MEASURED value with zero slack. At 4 this tool happily wrote an entry that then
# failed `test_short_denylist_entries_do_not_increase` — and because the hygiene
# lane has no path filter, that failure lands on EVERY open PR in the repo, with
# a hashed list nobody can read to find the culprit.
#
# It is also the honest floor. The stored length narrows a brute-force search, so
# a short name is recoverable from its hash in seconds: adding one publishes the
# name about as well as leaving it in the code did.
MIN_LENGTH = 8


def normalise(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _is_dictionary_word(key: str) -> bool:
    """Is this normalised name also an ordinary English word?

    Advisory only — five entries already on the list are dictionary words and
    none of them appears in the tree, so this warns rather than refuses.
    """
    words = Path("/usr/share/dict/words")
    if not words.is_file():
        return False
    try:
        return any(line.strip().lower() == key for line in words.open())
    except OSError:
        return False


def word_count(name: str) -> int:
    return len(re.findall(r"[A-Za-z0-9]+", name))


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2

    text = DENYLIST.read_text()
    existing = set()
    for lineno, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(":")
        if len(parts) != 3:
            # Was `line.split(":")[2]` — IndexError, no context, on a file the
            # tool is meant to be the safe way to edit.
            print(f"error: {DENYLIST.name} line {lineno} is malformed "
                  f"({len(parts)} fields, expected 3)", file=sys.stderr)
            return 1
        existing.add(parts[2])

    added = []
    for raw in argv:
        key = normalise(raw)
        if len(key) < MIN_LENGTH:
            print(f"skip {raw!r}: normalises to {key!r}, under {MIN_LENGTH} chars. "
                  "Short entries match ordinary words AND are cheap to recover "
                  "from the stored length, so listing one publishes the name it "
                  "was meant to hide. Use a longer distinctive phrase — the full "
                  "company name rather than an abbreviation.", file=sys.stderr)
            continue
        if _is_dictionary_word(key):
            # A denylisted ordinary word turns every tracked file into an
            # offender at once, on every PR in the repo, with no way to see
            # which entry did it.
            print(f"WARNING {raw!r}: {key!r} is an ordinary dictionary word. "
                  "If it appears anywhere in the tree this will fail CI "
                  "repo-wide. Verify with the post-write scan below.",
                  file=sys.stderr)
        digest = hashlib.sha256(key.encode()).hexdigest()
        if digest in existing:
            print(f"skip {raw!r}: already present")
            continue
        added.append(f"{word_count(raw)}:{len(key)}:{digest}")
        existing.add(digest)

    if not added:
        return 0

    lines = text.rstrip("\n").split("\n")
    header = [ln for ln in lines if ln.startswith("#")]
    entries = sorted(set([ln for ln in lines if ln and not ln.startswith("#")] + added))
    header = [
        re.sub(r"^# \d+ entries\.$", f"# {len(entries)} entries.", ln) for ln in header
    ]
    DENYLIST.write_text("\n".join(header + entries) + "\n")
    print(f"added {len(added)} entr{'y' if len(added) == 1 else 'ies'}; "
          f"{len(entries)} total")

    # RE-RUN THE GUARD, so the blast radius is discovered here rather than in
    # someone else's CI. The hygiene lane has no path filter: a bad entry — an
    # ordinary word, or a name that turns out to be all over the tree — fails
    # EVERY open PR in the repo, and because the list is hashed nobody can tell
    # which entry did it. Ten seconds now beats that.
    print("\nre-running the hygiene guard against the new list…")
    guard = Path(__file__).resolve().parents[1] / "tests" / "test_public_repo_hygiene.py"
    result = subprocess.run(
        [sys.executable, str(guard)], capture_output=True, text=True
    )
    print(result.stdout.strip() or result.stderr.strip())
    if result.returncode != 0:
        print(
            "\nThe guard now FAILS. The names you added appear in the tree. "
            "Either scrub those files in the same change, or revert this edit "
            "to the denylist — do not leave it for someone else's PR to hit.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
