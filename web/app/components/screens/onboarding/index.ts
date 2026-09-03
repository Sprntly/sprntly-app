export { YourName } from "./YourName"
// The four-step flow (2026-09-03): company → connectors → review →
// personalize, then the unnumbered define-metrics sub-flow. import-context,
// api-key, product, workspace, metrics and invite were removed and their
// screens deleted with them — everything they collected is edited in
// Settings, and the workspace they used to ask you to name is created as
// "Main workspace". Bulk teammate invite (paste + CSV) moved to Settings →
// Team & roles rather than being dropped. See lib/onboarding/types.ts for the
// full map of what went where.
export { CompanyStep } from "./CompanyStep"
// Metrics is not a numbered route — its candidate seeding/merging helpers are
// reused by the define-metrics sub-flow, which is why it outlived the metrics
// STEP deleted around it.
export { Metrics } from "./Metrics"
export { Connectors } from "./Connectors"
export { ReviewStep } from "./ReviewStep"
export { PersonalizeStep } from "./PersonalizeStep"
// DefineMetrics is not a numbered route — the personalize step hands off to it
// and it completes onboarding (definitions + first brief + completion stamp).
export { DefineMetrics } from "./DefineMetrics"
// FirstBrief is retired from the numbered flow; kept exported for its test.
export { FirstBrief } from "./FirstBrief"
