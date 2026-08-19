"""READ-ONLY: has Sprntly ever emailed a real person at a given domain?

Sends nothing. Lists what Resend has delivered and filters by recipient domain,
so "did our testing reach a real customer's staff" is answered from the
provider's own record rather than from our tables — connector-health alerts and
Supabase auth mail never touch a Sprntly table, so they are invisible locally.

    python scripts/resend_audit.py --domain acme-logistics.example
"""
from __future__ import annotations

import argparse
import json
import os
import re
import urllib.error
import urllib.request


def api_key() -> str:
    key = os.environ.get("RESEND_API_KEY", "").strip()
    if not key:
        env = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
        if os.path.isfile(env):
            with open(env) as fh:
                m = re.search(r"^RESEND_API_KEY=(.+)$", fh.read(), re.M)
                if m:
                    key = m.group(1).strip().strip('"').strip("'")
    if not key:
        raise SystemExit("no RESEND_API_KEY in env or backend/.env")
    return key


def get(path: str, key: str) -> dict:
    req = urllib.request.Request(
        f"https://api.resend.com{path}",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", required=True)
    ap.add_argument("--limit", type=int, default=100)
    a = ap.parse_args()
    key = api_key()
    dom = a.domain.lower()

    try:
        payload = get(f"/emails?limit={a.limit}", key)
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:300]
        print(f"GET /emails failed: HTTP {e.code} {body}")
        print(
            "\nIf this is 404/405, this Resend plan does not expose email listing "
            "over the API — the send log is dashboard-only, and the audit has to "
            "be done at resend.com/emails with a filter on the domain."
        )
        raise SystemExit(2)

    items = payload.get("data") or []
    print(f"emails returned by the API: {len(items)}")
    hits = []
    for e in items:
        to = e.get("to") or []
        to = [to] if isinstance(to, str) else to
        if any(str(t).lower().endswith("@" + dom) for t in to):
            hits.append(e)

    print(f"delivered to @{dom}: {len(hits)}")
    for e in hits:
        print(
            f"  {e.get('created_at','?')}  to={e.get('to')}  "
            f"from={e.get('from')}  subject={e.get('subject')!r}  "
            f"status={e.get('last_event') or e.get('status')}"
        )
    if not hits:
        print(f"  (none in the {len(items)} most recent)")


if __name__ == "__main__":
    main()
