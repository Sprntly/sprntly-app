export { YourName } from "./YourName"
// v7 flow (screenshot spec 2026-07-21), keeping the optional api-key step the
// spec omits: company → product → metrics → api-key → connectors → workspace →
// invite → review → personalize, then the unnumbered define-metrics sub-flow
// completes onboarding. The api-key step is OPTIONAL/skippable (also editable
// in Settings → Admin).
export { CompanyStep } from "./CompanyStep"
export { ProductStep } from "./ProductStep"
export { MetricsStep } from "./MetricsStep"
export { ApiKey } from "./ApiKey"
// Metrics is not a numbered route — its helpers (candidate seeding/merging)
// are reused by MetricsStep. Kept exported for that reuse + tests.
export { Metrics } from "./Metrics"
export { Connectors } from "./Connectors"
export { WorkspaceStep } from "./WorkspaceStep"
export { InviteStep } from "./InviteStep"
export { ReviewStep } from "./ReviewStep"
export { PersonalizeStep } from "./PersonalizeStep"
// DefineMetrics is not a numbered route — the personalize step hands off to it
// and it completes onboarding (definitions + first brief + completion stamp).
export { DefineMetrics } from "./DefineMetrics"
// FirstBrief is retired from the numbered flow; kept exported for its test.
export { FirstBrief } from "./FirstBrief"
