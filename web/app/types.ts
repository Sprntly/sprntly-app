export type ScreenId =
  // Numbered onboarding steps, keyed by their semantic slug.
  | "ob-business-info"
  | "ob-metrics"
  | "ob-connectors"
  | "ob-coworkers"
  | "ob-first-brief"
  // Unnumbered loader interstitial — its own route, excluded from the numbered
  // ONBOARDING_SCREENS list and the progress dots.
  | "ob-analyzing"
  | "chat"
  | "brief"
  | "detail"
  | "prd"
  | "ondemand"
  | "past"
  | "shipped"
  | "settings"
  | "team"
  | "connectors"
  | "sources"
  | "tickets"
  // The dedicated full-page prototype surface. The prototype canvas renders
  // in-tab here at `/prototype?prd=<id>`; the PRD context rides as a query param.
  | "prototype"

// The NUMBERED onboarding screens, in flow order. `ob-analyzing` is deliberately
// absent — it is the unnumbered loader, not a counted step.
export const ONBOARDING_SCREENS: ScreenId[] = [
  "ob-business-info",
  "ob-metrics",
  "ob-connectors",
  "ob-coworkers",
  "ob-first-brief",
]

export const APP_SCREENS: ScreenId[] = [
  "chat",
  "brief",
  "detail",
  "prd",
  "ondemand",
  "past",
  "shipped",
  "settings",
  "team",
  // "connectors" removed in commit A — standalone route deleted, Settings →
  // Connectors is the sole surface. ScreenId kept in the type union for
  // the dormant ConnectorsScreen.tsx (salvaged in commit D).
  "sources",
  "tickets",
  "prototype",
]

/** Label for the main-column top chrome — align with sidebar nav labels where applicable. */
const MAIN_CHROME_TITLE: Record<ScreenId, string> = {
  "ob-business-info": "Setup · Step 1 of 5",
  "ob-metrics": "Setup · Step 2 of 5",
  "ob-connectors": "Setup · Step 3 of 5",
  "ob-coworkers": "Setup · Step 4 of 5",
  "ob-first-brief": "Setup · Step 5 of 5",
  // Unnumbered loader — no step counter.
  "ob-analyzing": "Setup",
  chat: "Home",
  brief: "Weekly brief",
  detail: "Evidence",
  prd: "PRD",
  ondemand: "Home",
  past: "Past briefs",
  shipped: "Shipped",
  settings: "Settings",
  team: "Team",
  connectors: "Connectors",
  sources: "Sources",
  tickets: "Project Management",
  prototype: "Prototype",
}

export function getMainChromeTitle(screen: ScreenId): string {
  return MAIN_CHROME_TITLE[screen]
}

/** All three contextual screens use an inline chat column; global overlay is not used. */
export const AI_BAR_SCREENS: ScreenId[] = []

export const AI_CONTEXTS: Record<
  string,
  { path: string; suggest: string[] }
> = {
  chat: {
    path: "/",
    suggest: [
      "Open this week's brief",
      "Help me prioritize my roadmap",
      "What should I focus on today?",
    ],
  },
  brief: {
    path: "/brief",
    suggest: [
      "Why is #01 ranked higher than #02?",
      "Show the raw signals behind the SMS issue",
      "Compare this brief to last week's",
    ],
  },
  detail: {
    path: "/evidence",
    suggest: [
      "Run a sensitivity analysis on the revenue model",
      "Pull more similar tickets",
      "Who has context on SMS verification?",
    ],
  },
  prd: {
    path: "/prd",
    suggest: [
      "Make the test plan more rigorous",
      "Add rollback criteria",
      "Who should own this?",
    ],
  },
  ondemand: {
    path: "/",
    suggest: [
      "Generate a Q3 strategy",
      "Draft a PRD for team folder permissions",
      "Compare retention across our top 3 segments",
    ],
  },
  past: {
    path: "/past",
    suggest: [
      "Which finding type ships most?",
      "Any declined findings worth reconsidering?",
    ],
  },
  shipped: {
    path: "/shipped",
    suggest: [
      "What moved our core metric most?",
      "Which shipped items underperformed estimates?",
    ],
  },
  settings: {
    path: "/settings",
    suggest: [
      "Recommend a delivery cadence for my role",
      "Should I upgrade to Growth?",
    ],
  },
  team: {
    path: "/team",
    suggest: ["Who opens the brief most often?", "Suggest who to invite from Slack"],
  },
  // connectors AI_CONTEXTS entry removed in commit A (no standalone route).
  // When the Settings → Connectors pane lands in commit D, decide whether to
  // surface AI suggestions inside the settings shell or drop them entirely.
  sources: {
    path: "/sources",
    suggest: [
      "Which source contributed the most to last week's brief?",
      "Are any sources stale or duplicated?",
    ],
  },
  tickets: {
    path: "/tickets",
    suggest: [
      "Which ticket has the highest impact?",
      "Show me all high priority tickets",
    ],
  },
}

export const CONNECTOR_STAGES = [
  "analytics",
  "feedback",
  "calls",
  "revenue",
  "reviews",
  "pm",
  "code",
] as const

export type ConnectorStage = (typeof CONNECTOR_STAGES)[number]

export const STAGE_LABELS: Record<ConnectorStage, string> = {
  analytics: "Product analytics",
  feedback: "Customer feedback",
  calls: "Calls & conversations",
  revenue: "Revenue & CRM",
  reviews: "Reviews & store",
  pm: "Project management",
  code: "Code",
}
