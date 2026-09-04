// The first-run product tour: what it says, what it points at, and when a
// step is withheld.
//
// ACCURACY IS THE WHOLE POINT. Every line below describes behaviour that was
// read out of the code rather than assumed, and the `when` predicates exist so
// the tour never points at something this particular viewer does not have:
//
//   * MODULES ARE PER-COMPANY. `companies.feature_flags` can switch off
//     `agents` — all chat capability — see backend/app/entitlements.py, which
//     enforces it server-side. Explaining the composer to a company whose
//     chat route 403s promises a feature they do not have.
//   * FAIL OPEN, like the backend does. `parseFeatureFlags` merges
//     DEFAULT_FEATURE_FLAGS (agents: true), so a company predating a flag
//     keeps the step. Only an explicit `false` withholds one.
//
// A step whose anchor is missing from the DOM is not an error — it renders
// centred instead (see ProductTour). That is what carries the tour below
// 900px, where globals.css sets `.sidebar { display: none }` and every rail
// anchor genuinely is absent.
import type { FeatureFlags } from "../../lib/onboarding/types"

/** What the tour knows about the person watching it.
 *
 *  Only `flags` is read by a step today (the three-step cut retired the
 *  role-, trial- and workspace-gated ones). The rest stay because
 *  `ProductTour` already resolves them and a step that needs to know who it is
 *  talking to should not have to re-plumb the audience to find out.
 */
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
  /** How many workspaces this person can enter. With one, and no right to
   *  create another, the rail's switcher renders STATIC and unclickable
   *  (`wsInteractive` in Sidebar) — so the step that explains it is withheld
   *  rather than spotlighting a control that does not open. */
  workspaceCount: number
}

export type TourStep = {
  /** Stable id — used as the React key and in the completion test. */
  id: string
  /** `data-tour` value of the element to spotlight. Omit for a centred step —
   *  the opener, which is about the product rather than any one control. A
   *  named anchor that is missing also renders centred. */
  anchor?: string
  title: string
  /** One or two sentences. Plain text — no markup, so a step can never
   *  smuggle HTML into the overlay. */
  body: string
  /** Withhold the step when this returns false. Absent = always shown. */
  when?: (a: TourAudience) => boolean
}

/**
 * The steps, in order. THREE OF THEM (owner's call, 2026-09-04).
 *
 * It was twelve: a walk down the rail, one step per screen, plus a welcome and
 * a sign-off. Every line of it was true, and almost none of it was worth a
 * first-run interruption — a tour that names Backlog, Search, Sync and the
 * workspace switcher before the reader has asked the product anything teaches
 * the furniture rather than the product.
 *
 * What survives is what someone has to know to get value on day one: what
 * Sprntly is FOR, the one question shape that shows it working, and the
 * container their team works in. Everything else is discoverable from the
 * rail, which is what a rail is for.
 *
 * NO BOOKENDS ANY MORE. The old first and last steps existed to open and close
 * a twelve-step sequence; at three, a "that's the tour" card is a third of the
 * tour spent saying it is over. The first step carries the framing instead.
 */
export const TOUR_STEPS: TourStep[] = [
  {
    id: "welcome",
    // NOT "Welcome to Sprntly". This runs for every user who has never been
    // shown it, which on the day it ships is EVERYONE — including people who
    // have been using the product for months. Welcoming them to software they
    // already use reads as a system that does not know who it is talking to.
    title: "What Sprntly is for",
    body:
      "Sprntly helps you collaborate with your team and identify the most "
      + "important thing to build to drive your business goals. Three quick "
      + "things — press Escape to leave at any point.",
  },
  {
    id: "ask",
    anchor: "composer",
    title: "Ask for a plan, not just an answer",
    body:
      "Ask a question like “How do I drive my core metrics by x%” and Sprntly "
      + "creates a plan and helps you develop a strategy to achieve the goal.",
    // `agents` is ALL chat capability (entitlements.py). With it off there is
    // nothing to demonstrate here and the route refuses the call anyway.
    when: (a) => a.flags.agents !== false,
  },
  {
    id: "projects",
    anchor: "nav-projects",
    title: "Projects",
    body:
      "Projects let you collaborate with your team: invite them, share "
      + "context, and work alongside your teammates and agents.",
  },
]

/** The steps this particular viewer should see, in order. */
export function stepsFor(audience: TourAudience): TourStep[] {
  return TOUR_STEPS.filter((s) => (s.when ? s.when(audience) : true))
}
