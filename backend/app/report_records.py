"""Shared JSON-record salvage for the web-research report pipelines.

Both `app.public_feedback` and `app.competitive_intel` run a CAPTURE pass whose
output is "ONLY a JSON array of record objects". Models mostly comply, but two
failure shapes recur and neither may cost us a multi-minute web sweep:

  * stray prose or a ```json fence wrapped around the array, and
  * an array cut off mid-record by the output-token budget.

`parse_records` tolerates both: it tries the plausible slices, then trims a
truncated array back to the last complete record. It returns [] only when
nothing parseable is present — which is why callers pair it with the capture's
`stop_reason`: an empty parse on a truncated response means "the capture
overflowed", never "nothing was found".

Extracted from public_feedback._parse_records (which now delegates here) so the
competitive-intelligence capture reuses the identical salvage rather than a
second copy that drifts.
"""
from __future__ import annotations

import json
import re


def parse_records(text: str) -> list[dict]:
    """Extract the JSON array of records from a capture pass's output.

    The model is instructed to emit only the array, but tolerate stray
    prose/fences around it, and salvage the complete prefix of an array
    truncated by the output budget. Returns [] when nothing parseable is found.
    """
    text = (text or "").strip()
    if not text:
        return []
    candidates = [text]
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        candidates.append(fence.group(1).strip())
    # First "[" can be prose ("we searched [several] sites"), so also anchor on
    # the first "[{" — the actual start of an array of objects.
    start, end = text.find("["), text.rfind("]")
    if start != -1 and end > start:
        candidates.append(text[start:end + 1])
    arr = re.search(r"\[\s*\{", text)
    if arr and end > arr.start():
        candidates.append(text[arr.start():end + 1])
    for cand in candidates:
        try:
            parsed = json.loads(cand)
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, list):
            return [r for r in parsed if isinstance(r, dict)]
    # Truncation salvage: from the array start, trim back to each closing
    # brace until the prefix + "]" parses — keeps every complete record.
    if arr:
        body = text[arr.start():]
        while True:
            last = body.rfind("}")
            if last == -1:
                return []
            try:
                parsed = json.loads(body[:last + 1] + "]")
            except ValueError:
                body = body[:last]
                continue
            return [r for r in parsed if isinstance(r, dict)]
    return []
