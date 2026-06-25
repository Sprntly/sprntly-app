export { YourName } from "./YourName"
export { BusinessInfo } from "./BusinessInfo"
export { Analyzing } from "./Analyzing"
// Metrics is no longer a standalone numbered route — its pick-3 view + helpers
// are reused inline by BusinessInfo (onb1). Kept exported for that reuse + tests.
export { Metrics } from "./Metrics"
export { Connectors } from "./Connectors"
export { BusinessContext } from "./BusinessContext"
// Strategy is the FINAL numbered step (onbstrat) — it completes onboarding +
// kicks the first brief. Workspace (onbws) is now an early, name-only step.
export { Strategy } from "./Strategy"
export { Workspace } from "./Workspace"
// FirstBrief is retired from the numbered flow; brief generation + completion
// live on the Strategy step (onbstrat). Kept exported for its existing test.
export { FirstBrief } from "./FirstBrief"
