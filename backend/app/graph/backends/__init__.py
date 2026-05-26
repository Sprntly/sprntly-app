"""KG backends — concrete implementations of GraphBackend."""
from app.graph.backends.base import GraphBackend
from app.graph.backends.sqlite_backend import SqliteBackend

__all__ = ["GraphBackend", "SqliteBackend"]


def get_backend(name: str, **kwargs):
    """Factory. Imports the falkor backend lazily so the graphiti/falkordb
    pip deps are only required when actually using it."""
    if name == "sqlite":
        return SqliteBackend(**kwargs)
    if name == "falkor":
        from app.graph.backends.falkor_backend import FalkorBackend

        return FalkorBackend(**kwargs)
    raise ValueError(f"Unknown graph backend: {name}")
