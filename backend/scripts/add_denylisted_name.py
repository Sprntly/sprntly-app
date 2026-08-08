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
import sys
from pathlib import Path

DENYLIST = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "customer_name_denylist.txt"

MIN_LENGTH = 4


def normalise(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def word_count(name: str) -> int:
    return len(re.findall(r"[A-Za-z0-9]+", name))


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2

    text = DENYLIST.read_text()
    existing = {
        line.split(":")[2]
        for line in text.splitlines()
        if line.strip() and not line.startswith("#")
    }

    added = []
    for raw in argv:
        key = normalise(raw)
        if len(key) < MIN_LENGTH:
            print(f"skip {raw!r}: normalises to {key!r}, under {MIN_LENGTH} chars "
                  "— too short to match without false positives", file=sys.stderr)
            continue
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
