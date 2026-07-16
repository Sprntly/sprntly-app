export { YourName } from "./YourName"
// 2026-07 registration-spec flow: company → product → metrics → api-key →
// connectors → team → strategy → workspace (the old combined BusinessInfo,
// the early name-only Workspace, and the onboarding BusinessContext review
// are retired — business context is drafted in the background and edited in
// Settings).
export { CompanyStep } from "./CompanyStep"
export { ProductStep } from "./ProductStep"
export { MetricsStep } from "./MetricsStep"
// Metrics is not a numbered route — its helpers (candidate seeding/merging)
// are reused by MetricsStep. Kept exported for that reuse + tests.
export { Metrics } from "./Metrics"
export { ApiKey } from "./ApiKey"
export { Connectors } from "./Connectors"
export { TeamStep } from "./TeamStep"
export { Strategy } from "./Strategy"
// WorkspaceStep is the FINAL numbered step — names the real workspaces row,
// completes onboarding, and kicks the first brief.
export { WorkspaceStep } from "./WorkspaceStep"
// FirstBrief is retired from the numbered flow; kept exported for its test.
export { FirstBrief } from "./FirstBrief"
