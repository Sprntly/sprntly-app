"""Schema is owned by Supabase now — this module is a back-compat shim.

The DDL lives under `~/Sprntly/supabase/migrations/` and is applied via
the Supabase management API or the dashboard SQL editor. Nothing in
this codebase issues CREATE TABLE statements at runtime anymore.

`init_db()` and `SCHEMA` are kept as no-op / empty so existing callers
(`app/main.py` lifespan, `app/cli.py` commands, tests) don't break.
"""

# Empty string so anything reading `app.db.schema.SCHEMA` doesn't crash.
SCHEMA: str = ""


def init_db() -> None:
    """No-op. Schema is provisioned out-of-band via Supabase migrations."""
    return None
