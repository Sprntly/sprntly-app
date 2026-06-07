"""DS (data science) pilot — structured analyses + the Dashboard metrics service.

Pilot-1 computes weekly aggregates from connected providers' structured data,
persists ONLY tiny rolling aggregates (`metric_points` — one number per metric
per period per source) plus distilled Findings (anomalies → kg_signal rows).
Provider rows themselves are pulled transiently and never bulk-copied (the
data-minimization rule).
"""
