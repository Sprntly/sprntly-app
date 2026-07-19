export { YourName } from "./YourName"
// v6 flow (screenshot spec 2026-07-17) + the restored optional api-key step
// (2026-07-19): company → product → metrics → api-key → connectors → team →
// strategy → decisions → invite → review, then the unnumbered define-metrics
// sub-flow completes onboarding. The api-key step is OPTIONAL/skippable (also
// editable in Settings → Admin). The closing workspace-naming step stays
// retired (the default workspace stays "Default"; Settings → Workspaces
// renames it).
export { CompanyStep } from "./CompanyStep"
export { ProductStep } from "./ProductStep"
export { MetricsStep } from "./MetricsStep"
export { ApiKey } from "./ApiKey"
// Metrics is not a numbered route — its helpers (candidate seeding/merging)
// are reused by MetricsStep. Kept exported for that reuse + tests.
export { Metrics } from "./Metrics"
export { Connectors } from "./Connectors"
export { TeamStep } from "./TeamStep"
export { Strategy } from "./Strategy"
export { DecisionsStep } from "./DecisionsStep"
export { InviteStep } from "./InviteStep"
export { ReviewStep } from "./ReviewStep"
// DefineMetrics is not a numbered route — the review step hands off to it and
// it completes onboarding (definitions + first brief + completion stamp).
export { DefineMetrics } from "./DefineMetrics"
// FirstBrief is retired from the numbered flow; kept exported for its test.
export { FirstBrief } from "./FirstBrief"
