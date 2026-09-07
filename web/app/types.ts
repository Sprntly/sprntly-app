export type ScreenId =
  // Numbered onboarding steps, keyed by their semantic slug — 6 steps as of
  // 2026-09-07 (invite reinstated, payment moved to the end): company →
  // connectors → invite → review → personalize → plan, with the unnumbered
  // define-metrics sub-flow between the last two when analytics is connected.
  // The plan step is where onboarding completes. See ONBOARDING_STEP_SLUGS for
  // the full history of what was cut, why invite came back, and why payment
  // stopped being the second thing anyone saw.
  | "ob-company"
  | "ob-connectors"
  | "ob-invite"
  | "ob-review"
  | "ob-personalize"
  | "ob-plan"
  | "chat"
  | "chats"
  // The Artifacts library — a dedicated left-nav surface listing durable outputs
  // (PRDs, prototypes, evidence). Previously a tab inside History; now stands on
  // its own so History holds only chats.
  | "artifacts"
  | "brief"
  | "detail"
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
  | "ideation"
  // Top-level "what good looks like" surface: the company's gold-standard PRD
  // examples. Design data-view="templates", bookmark icon.
  | "templates"
  // The Skills gallery — every routable PM skill as a card; clicking one opens
  // a chat thread with the skill's /trigger pre-filled in the composer.
  | "skills"
  // Projects — a shared container gathering a topic's artifacts + the group/
  // individual chats and memory behind them. Coexists with Artifacts and
  // Ideation (AD-P14); flat route `/projects`, detail selected by `?id=`.
  | "projects"

// The NUMBERED onboarding screens, in flow order.
export const ONBOARDING_SCREENS: ScreenId[] = [
  "ob-company",
  "ob-connectors",
  "ob-invite",
  "ob-review",
  "ob-personalize",
  "ob-plan",
]

export const APP_SCREENS: ScreenId[] = [
  "chat",
  "chats",
  "artifacts",
  "brief",
  "detail",
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
  "ideation",
  "templates",
  "skills",
  "projects",
]

/** Label for the main-column top chrome — align with sidebar nav labels where applicable. */
const MAIN_CHROME_TITLE: Record<ScreenId, string> = {
  "ob-company": "Setup · Step 1 of 6",
  "ob-connectors": "Setup · Step 2 of 6",
  "ob-invite": "Setup · Step 3 of 6",
  "ob-review": "Setup · Step 4 of 6",
  "ob-personalize": "Setup · Step 5 of 6",
  "ob-plan": "Setup · Step 6 of 6",
  chat: "Home",
  chats: "History",
  artifacts: "Artifacts",
  brief: "Top Insights",
  detail: "Evidence",
  ondemand: "Home",
  past: "Past briefs",
  shipped: "Shipped",
  settings: "Settings",
  team: "Team",
  connectors: "Connectors",
  sources: "Sources",
  tickets: "Project Management",
  prototype: "Prototype",
  ideation: "Backlog",
  templates: "Templates",
  skills: "Skills",
  projects: "Projects",
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
