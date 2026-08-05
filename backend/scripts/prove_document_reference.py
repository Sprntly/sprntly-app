"""Prove Stage R against a REAL company's catalogued documents. Read-only.

Why this exists: `document_reference.resolve_documents` is pure and takes its
candidate rows as an argument, so it can be exercised against production
metadata without a Confluence token, without an LLM call, and without writing
anything. That makes it possible to show what chat WOULD resolve for a real
workspace before anyone clicks through a browser.

WHAT IT TOUCHES: one SELECT against `document_catalog`, filtered to the company
you name. Nothing else. No writes, no connector calls, no model calls.

PRIVACY: prints document TITLES and PROVIDERS only — never summaries, topics or
body text. The catalog holds real customer documents; a proof that pastes their
contents into a report is not a proof worth having.

USAGE
    python scripts/prove_document_reference.py <company_id> [--json]

Reads SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY from the environment (or
backend/.env). Exits non-zero if the company has no catalogued documents, so
this doubles as a check that a workspace is actually provisioned for the
document path.
"""
from __future__ import annotations

import json
import os
import sys


def _load_documents(company_id: str) -> list:
    from app.document_catalog import list_documents

    return list_documents(company_id)


def _scenarios(docs: list) -> list[tuple[str, str, list | None]]:
    """Cases chosen from the company's OWN titles, so this is a real test and
    not a rehearsal against invented data.

    Each is (label, message, history). The naming case borrows distinctive
    words from the most word-rich title; the ambiguous case is built from a
    genuine near-collision if the workspace has one.
    """
    from app.document_reference import query_terms

    titles = [d.title for d in docs if (d.title or "").strip()]

    # A title with the most distinctive (non-ask-word) terms makes the
    # clearest naming case.
    ranked = sorted(titles, key=lambda t: len(query_terms(t)), reverse=True)
    named_title = ranked[0] if ranked else ""
    named_terms = query_terms(named_title)[:3]
    named_msg = (
        f"what does our wiki say about {' '.join(named_terms).lower()}?"
        if named_terms else "what does our wiki say about onboarding?"
    )

    # A real near-collision: two titles sharing >= 2 distinctive words.
    ambiguous_msg = ""
    for i, a in enumerate(titles):
        for b in titles[i + 1:]:
            shared = set(w.lower() for w in query_terms(a)) & set(
                w.lower() for w in query_terms(b)
            )
            if len(shared) >= 2:
                ambiguous_msg = (
                    f"what does the {' '.join(sorted(shared)).lower()} page say?"
                )
                break
        if ambiguous_msg:
            break

    established = named_title or "the onboarding page"
    cases: list[tuple[str, str, list | None]] = [
        ("(a) NAMED — implicit, never spells the title", named_msg, None),
        (
            "(b) ANAPHORIC — follow-up against the established doc",
            "what does it say about the process?",
            [
                {"role": "user", "content": f"summarize {established}"},
                {"role": "assistant", "content": "Here you go."},
            ],
        ),
        ("(d) CONTROL — ordinary question, must not resolve",
         "how many customers do we have?", None),
        ("(e) CONTROL — bare pronoun, no reading cue, must not resolve",
         "how many seats is it?",
         [{"role": "user", "content": "we bought more licences"}]),
        ("(f) OVERREACH GUARD — plural/general ask, must not resolve",
         "can you summarize our recent documents?", None),
    ]
    if ambiguous_msg:
        cases.insert(
            2, ("(c) AMBIGUOUS — real near-collision, must ABSTAIN",
                ambiguous_msg, None)
        )
    return cases


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    as_json = "--json" in sys.argv
    if not args:
        print(__doc__)
        return 2
    company_id = args[0]

    docs = _load_documents(company_id)
    if not docs:
        print(f"No catalogued documents for {company_id} — nothing to prove.")
        return 1

    from app.document_reference import resolve_documents

    by_provider: dict[str, int] = {}
    for d in docs:
        by_provider[d.provider] = by_provider.get(d.provider, 0) + 1

    results = []
    for label, message, history in _scenarios(docs):
        ref = resolve_documents(message, docs, history=history)
        results.append({
            "case": label,
            "message": message,
            "history_turns": len(history or []),
            "resolved": [d.title for d in ref.documents],
            "providers": [d.provider for d in ref.documents],
            "basis": ref.basis,
            "abstained": ref.abstained,
            "reason": ref.reason,
            "candidates": [d.title for d in ref.candidates],
        })

    if as_json:
        print(json.dumps(
            {"company_id": company_id, "catalog": by_provider,
             "results": results}, indent=2,
        ))
        return 0

    print(f"company {company_id} — catalog: {by_provider} "
          f"({len(docs)} documents)\n")
    for r in results:
        print(r["case"])
        print(f'  message   : "{r["message"]}"')
        if r["history_turns"]:
            print(f'  history   : {r["history_turns"]} prior turns')
        if r["resolved"]:
            print(f'  RESOLVED  : {r["resolved"]} via {r["basis"]} '
                  f'[{", ".join(r["providers"])}]')
        elif r["abstained"]:
            print(f'  ABSTAINED : {r["reason"]}')
            if r["candidates"]:
                print(f'  candidates: {r["candidates"]}')
        else:
            print("  NO REFERENCE — chat answers normally, no document pinned")
        print()
    return 0


if __name__ == "__main__":
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    raise SystemExit(main())
