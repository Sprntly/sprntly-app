export type ScreenId =
  | "ob-1"
  | "ob-2"
  | "ob-3"
  | "ob-4"
  | "ob-5"
  | "ob-6"
  | "ob-7"
  | "ob-8"
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
  // The post-generation Design Agent canvas — the one refresh-stable deep-URL
  // screen (`/design/{prototype_id}`). Layered on top of the existing no-deep-URL
  // nav; see lib/routes.ts canvasPath / prototypeIdFromCanvasPath.
  | "da-canvas"

export const ONBOARDING_SCREENS: ScreenId[] = [
  "ob-1",
  "ob-2",
  "ob-3",
  "ob-4",
  "ob-5",
  "ob-6",
  "ob-7",
  "ob-8",
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
]

/** Label for the main-column top chrome — align with sidebar nav labels where applicable. */
const MAIN_CHROME_TITLE: Record<ScreenId, string> = {
  "ob-1": "Setup · Step 1 of 8",
  "ob-2": "Setup · Step 2 of 8",
  "ob-3": "Setup · Step 3 of 8",
  "ob-4": "Setup · Step 4 of 8",
  "ob-5": "Setup · Step 5 of 8",
  "ob-6": "Setup · Step 6 of 8",
  "ob-7": "Setup · Step 7 of 8",
  "ob-8": "Setup · Step 8 of 8",
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
  // The canvas renders as a full-screen overlay (its own breadcrumb), so this
  // chrome title is never actually shown; present only for Record<ScreenId>
  // completeness.
  "da-canvas": "Prototype",
}

export function getMainChromeTitle(screen: ScreenId): string {
  return MAIN_CHROME_TITLE[screen]
}

/** Bottom contextual ask bar — Brief, Evidence, PRD only. Home uses in-page chat. */
export const AI_BAR_SCREENS: ScreenId[] = ["brief", "detail", "prd"]

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
