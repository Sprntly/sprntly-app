"""Artifact format templates — a company's own PRD / ticket / engineering-spec
FORMS, uploaded once and adopted by the generators.

The domain layer sits in `store.py` (validation, caps, the write half); `db/
artifact_templates.py` owns persistence and `routes/artifact_templates.py` owns
HTTP. The compiler that turns an uploaded markdown format into a canonical
skeleton lands here later as `compile_prd.py` + `validate.py`; nothing in this
package reads the table on the generation path yet.
"""
