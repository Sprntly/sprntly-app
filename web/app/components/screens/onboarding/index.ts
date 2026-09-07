export { YourName } from "./YourName"
// The six-step flow (2026-09-07, invite reinstated and payment moved to the
// end): company → connectors → invite → review → personalize → plan, with the
// unnumbered define-metrics sub-flow between the last two when analytics is
// connected.
// import-context, api-key, product, workspace and metrics stay removed;
// everything they collected is edited in Settings, and the workspace they
// used to ask you to name is created as "Main workspace". Invite is back in
// the numbered flow AND still lives in Settings → Team & roles (bulk paste +
// CSV included, both callers sharing lib/teamApi.ts) — one is not a
// replacement for the other. See lib/onboarding/types.ts for the full map of
// what went where.
export { CompanyStep } from "./CompanyStep"
// Metrics is not a numbered route — its candidate seeding/merging helpers are
// reused by the define-metrics sub-flow, which is why it outlived the metrics
// STEP deleted around it.
export { Metrics } from "./Metrics"
export { Connectors } from "./Connectors"
export { InviteStep } from "./InviteStep"
export { ReviewStep } from "./ReviewStep"
export { PersonalizeStep } from "./PersonalizeStep"
export { PlanStep } from "./PlanStep"
// DefineMetrics is not a numbered route — the personalize step hands off to it
// and it hands on to the plan step, which completes onboarding.
export { DefineMetrics } from "./DefineMetrics"
// FirstBrief is retired from the numbered flow; kept exported for its test.
export { FirstBrief } from "./FirstBrief"
