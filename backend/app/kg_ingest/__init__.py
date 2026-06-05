"""Generic ingestion — connector pullers → RawRecord → extractor → KG (§1b)."""
from app.kg_ingest.runner import PULLERS, sync_provider
from app.kg_ingest.types import RawRecord

__all__ = ["PULLERS", "sync_provider", "RawRecord"]
