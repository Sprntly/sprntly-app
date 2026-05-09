"""CLI for ops tasks. Run with: python -m app.cli <subcommand>"""
import argparse
import json

from app.corpus import load_corpus
from app.db import get_current_brief, init_db, save_brief
from app.llm import call_json
from app.prompts import BRIEF_SYSTEM, BRIEF_USER_TEMPLATE


def cmd_init_db(args):
    init_db()
    print(f"DB initialized.")


def cmd_show_brief(args):
    brief = get_current_brief(args.dataset)
    if not brief:
        print(f"No current brief for dataset {args.dataset!r}.")
        return
    print(json.dumps(brief, indent=2))


def cmd_generate_brief(args):
    init_db()
    corpus = load_corpus(args.dataset)
    print(f"Loaded {len(corpus.docs)} corpus docs ({corpus.total_chars()} chars).")
    print(f"Calling Claude to generate brief for {args.dataset}...")
    user = BRIEF_USER_TEMPLATE.format(dataset=args.dataset, corpus=corpus.joined())
    payload = call_json(system=BRIEF_SYSTEM, user=user)
    week_label = payload.get("week_label", "")
    insights = payload.get("insights") or []
    brief_id = save_brief(
        dataset=args.dataset, week_label=week_label, payload=payload
    )
    print(
        f"Saved brief_id={brief_id}, week={week_label!r}, "
        f"insights={len(insights)} ({[i.get('tag') for i in insights]})"
    )


def main():
    ap = argparse.ArgumentParser(prog="app.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init-db", help="Create SQLite tables")
    p.set_defaults(fn=cmd_init_db)

    p = sub.add_parser("generate-brief", help="Run brief generation now (consumes Claude tokens)")
    p.add_argument("--dataset", default="asurion")
    p.set_defaults(fn=cmd_generate_brief)

    p = sub.add_parser("show-brief", help="Print the current cached brief")
    p.add_argument("--dataset", default="asurion")
    p.set_defaults(fn=cmd_show_brief)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
