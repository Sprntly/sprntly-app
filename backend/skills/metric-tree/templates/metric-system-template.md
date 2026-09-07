# Metric system — <product>

*Built top-down: North Star → supporting metrics (derived from the business) → driver tree → guardrails + health. Built <date>.*
*Legend: [NS] North Star · [L1] supporting · [driver] tree node · [guard] guardrail · [health] operational vital · ▲ leading input · ▼ lagging output.*

## North Star [NS]
**<the one metric>** — <why it captures customer value; value-capture test (rises only when customers benefit) + leading-indicator rationale>.

## Supporting metrics (3–6) [L1]
*Derived from: business model = <…>, segments = <…>, value moments = <…>. The smallest set that, if all moved, moves the NS.*

| Supporting metric | Why it drives the NS (from the business) | Link to NS | Lead/Lag | Owner |
|---|---|---|---|---|
| <metric> | <reason grounded in model/users/product> | ×/+/rate | ▲/▼ | <team> |
| <metric> | | | | |
*(3–6 rows.)*

## Driver tree (L2…Ln)
*Decompose each supporting metric with real operators; stop at a team-controllable lever or an instrumented event.*

```
<NS>
└─ <L1 supporting>            [L1] ▲  owner:<>
   = <child> × <child> × rate(<child>)
     ├─ <driver>              [driver]  owner:<>   event:<instrumented? / "to instrument">
     └─ <driver>              [driver]  owner:<>
```
*(Repeat per supporting metric. Note which leaves are backed by real instrumentation vs. "to instrument".)*

## Guardrails [guard] — must NOT degrade while chasing the NS
| Guardrail | Why (what gaming it would cause) | Threshold/direction | Owner |
|---|---|---|---|
| <quality/trust/unit-econ/latency metric> | | don't drop below / don't exceed | <team> |

## Health metrics [health] — operational vitals (outside the value chain)
| Health metric | Signals | Owner |
|---|---|---|
| <reliability/error/perf/support/freshness/cost> | system is sound | <team> |

## Leverage & instrumentation
- **Highest-leverage node:** <node> — <why a realistic % change there moves the NS most; precise if values exist, directional if not>.
- **Instrumentation gaps:** <leaves with no backing event → hand to analytics-instrumentation>.
