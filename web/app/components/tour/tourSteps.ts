// The first-run product tour: what it says, what it points at, and when a
// step is withheld.
//
// ACCURACY IS THE WHOLE POINT. Every line below describes behaviour that was
// read out of the code rather than assumed, and the `when` predicates exist so
// the tour never points at something this particular viewer does not have:
//
//   * MODULES ARE PER-COMPANY. `companies.feature_flags` can switch off
//     `agents` (all chat capability) and `top_insights` (the brief) — see
//     backend/app/entitlements.py, which enforces them server-side. A step
//     explaining a module that is off would highlight a rail item that does
//     not exist, or worse, promise a feature whose route 403s.
//   * ROLES DIFFER. Connecting data is an owner/admin job; a plain member
//     shown "connect your tools" is being pointed at a door they cannot open.
//   * FAIL OPEN, like the backend does. `parseFeatureFlags` merges
//     DEFAULT_FEATURE_FLAGS (agents: true, top_insights: true), so a company
//     predating a flag keeps the step. Only an explicit `false` withholds one.
//
// A step whose anchor is missing from the DOM is not an error — it renders
// centred instead (see ProductTour). That is what carries the tour below
// 900px, where globals.css sets `.sidebar { display: none }` and every rail
// anchor genuinely is absent.
import type { FeatureFlags } from "../../lib/onboarding/types"

/** What the tour knows about the person watching it. */
export type TourAudience = {
  flags: FeatureFlags
  /** `orgRole` from WorkspaceContext — "owner" | "admin" | "member" | null. */
  orgRole: string | null
  /** Their first name, for the opening line. Null degrades to a greeting with
   *  no name rather than "Welcome, ". */
  firstName: string | null
  /** True while `subscription_status` is "trialing" — the credits step only
   *  earns its place when there is a trial balance to talk about. */
  onTrial: boolean
}

export type TourStep = {
  /** Stable id — used as the React key and in the completion test. */
  id: string
  /** `data-tour` value of the element to spotlight. Omit for a centred step
   *  (the welcome and the sign-off, which are about the product, not a
   *  control). A named anchor that is missing also renders centred. */
  anchor?: string
  title: string
  /** One or two sentences. Plain text — no markup, so a step can never
   *  smuggle HTML into the overlay. */
  body: string
  /** Withhold the step when this returns false. Absent = always shown. */
  when?: (a: TourAudience) => boolean
}

const isAdmin = (a: TourAudience) => a.orgRole === "owner" || a.orgRole === "admin"

/**
 * The steps, in order.
 *
 * Ordered as a story rather than as a tour of the rail: what the product is,
 * the one thing you do most (ask), the things it produces for you, where they
 * collect, then the housekeeping controls. Someone who quits after step three
 * has still learned the part that matters.
 */
export const TOUR_STEPS: TourStep[] = [
  {
    id: "welcome",
    // NOT "Welcome to Sprntly". This runs for every user who has never been
    // shown it, which on the day it ships is EVERYONE — including people who
    // have been using the product for months. Welcoming them to software they
    // already use reads as a system that does not know who it is talking to.
    // "A quick tour" is true for a first-timer and a veteran alike.
    title: "A quick tour of Sprntly",
    body:
      "Sprntly reads the tools your team already works in and turns what it "
      + "finds into product decisions — answers, briefs, PRDs and prototypes. "
      + "This takes about a minute. You can leave at any point with Escape.",
  },
  {
    id: "ask",
    anchor: "composer",
    title: "Start by asking",
    body:
      "Ask a question in plain English and you get an answer grounded in your "
      + "connected data, with its sources. Type “/” to pick a specific "
      + "method instead — a PRD, a competitive report, a persona — and Sprntly "
      + "runs that one.",
    // `agents` is ALL chat capability (entitlements.py). With it off there is
    // nothing to demonstrate here and the route refuses the call anyway.
    when: (a) => a.flags.agents !== false,
  },
  {
    id: "brief",
    anchor: "nav-brief",
    title: "Top Insights",
    body:
      "A ranked read on what needs attention, rebuilt on a schedule from "
      + "everything you have connected. It fills in after your first sync — "
      + "an empty list here means the data has not landed yet, not that there "
      + "is nothing to say.",
    when: (a) => a.flags.top_insights !== false,
  },
  {
    id: "artifacts",
    anchor: "nav-artifacts",
    title: "Everything it writes lands here",
    body:
      "Artifacts is the library for every document Sprntly generates — PRDs, "
      + "prototypes, evidence reports, ticket sets and uploads — with tabs to "
      + "filter by kind. If you made it and lost it, it is in here.",
  },
  {
    id: "projects",
    anchor: "nav-projects",
    title: "Projects keep work together",
    body:
      "A project has its own chat and its own tasks, so a piece of work keeps "
      + "its context instead of scattering across threads. Your first PRD "
      + "starts one automatically.",
  },
  {
    id: "backlog",
    anchor: "nav-backlog",
    title: "Backlog is the idea list",
    body:
      "Ideas ranked by impact, with the finished ones kept alongside. You can "
      + "generate a full PRD straight from an idea rather than starting from "
      + "a blank page.",
  },
  {
    id: "sync",
    anchor: "rail-sync",
    title: "Bring your data up to date",
    body:
      "This runs a full pass over everything you have connected — the same run "
      + "that happens on a schedule. Clicking it while one is already going "
      + "joins that run rather than starting a second.",
  },
  {
    id: "connect",
    anchor: "rail-settings",
    title: "Connect your tools",
    body:
      "Settings is where you connect Google Drive, Slack, Jira, GitHub, Figma "
      + "and the rest. Sprntly is only as good as what it can read, so this is "
      + "the highest-value thing you can do next.",
    // A plain member cannot manage connectors; pointing them at it is
    // pointing at a door they cannot open.
    when: isAdmin,
  },
  {
    id: "search",
    anchor: "rail-search",
    title: "Find anything",
    body:
      "Search reaches every screen, chat, document and method by name — and "
      + "Ctrl+K (⌘K on a Mac) opens it from wherever you are. It is the "
      + "fastest way around the product once you know what you are after.",
  },
  {
    id: "credits",
    anchor: "sidebar-trial",
    title: "What your trial covers",
    body:
      "Generating costs credits, and your trial comes with a fixed amount — "
      + "enough to take one idea from a question through to a written PRD. "
      + "This counter shows what is left of the free week; your plan's monthly "
      + "credits start when it ends.",
    // Only while trialling: the anchor itself only renders then (Sidebar's
    // `trialDays != null` guard), and the copy is about the trial balance.
    when: (a) => a.onTrial,
  },
  {
    id: "done",
    title: "That's the tour",
    // Deliberately does not tell anyone to go and connect something: this also
    // runs for established users who connected their tools months ago, and an
    // instruction they have already followed makes the whole tour look like it
    // is reading from a script rather than at their workspace. The connector
    // point is made once, in its own step, to the admins who can act on it.
    body:
      "Ask it something you would normally go digging for — that is the "
      + "fastest way to see what it already knows. The more of your tools it "
      + "can read, the more it can answer. If anything looks wrong, the "
      + "feedback button in the bottom-left goes straight to us.",
  },
]

/** The steps this particular viewer should see, in order. */
export function stepsFor(audience: TourAudience): TourStep[] {
  return TOUR_STEPS.filter((s) => (s.when ? s.when(audience) : true))
}
