// What the first-run tour is allowed to say, and to whom.
//
// The tour is THREE STEPS (owner's call, 2026-09-04) — what Sprntly is for,
// the question shape that shows it working, and Projects. It was twelve, a
// walk down the rail; most of what went was furniture the rail already
// teaches. So most of what this file used to assert went with it: the
// role-gated connector step, the trial-credits step, the workspace switcher
// and the Top Insights step no longer exist to be filtered.
//
// What remains is the risk that survives any scripted tour: pointing at
// something this viewer does not have. That is now one gate — chat — plus the
// copy invariants that keep a step honest.
import { readFileSync, readdirSync } from "node:fs"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"

import { describe, expect, it } from "vitest"

import { DEFAULT_FEATURE_FLAGS } from "../../../lib/onboarding/types"
import { TOUR_STEPS, stepsFor, type TourAudience } from "../tourSteps"

function audience(over: Partial<TourAudience> = {}): TourAudience {
  return {
    flags: { ...DEFAULT_FEATURE_FLAGS },
    orgRole: "owner",
    firstName: "Ada",
    onTrial: true,
    workspaceCount: 1,
    ...over,
  }
}

const idsFor = (a: TourAudience) => stepsFor(a).map((s) => s.id)

describe("the tour is three steps", () => {
  it("says what Sprntly is for, what to ask it, and where teams work", () => {
    // The order is the argument: the product, then the one thing you do with
    // it, then the container your team works in. A reader who quits after the
    // first card has still been told what this is.
    expect(idsFor(audience())).toEqual(["welcome", "ask", "projects"])
  })

  it("opens with an anchorless step, and every other step points at something", () => {
    // The opener is about the product, not a control — spotlighting the rail
    // before saying what the product is teaches the furniture first.
    expect(TOUR_STEPS[0].anchor).toBeUndefined()
    for (const s of TOUR_STEPS.slice(1)) {
      expect(s.anchor, `step "${s.id}" has nothing to point at`).toBeTruthy()
    }
  })

  it("has unique ids", () => {
    const ids = TOUR_STEPS.map((s) => s.id)
    expect(new Set(ids).size).toBe(ids.length)
  })
})

describe("stepsFor — module entitlements", () => {
  // entitlements.py resolves FAIL-OPEN so a company predating a flag is not
  // silently downgraded. The tour has to match, or an established tenant loses
  // a step for a feature they demonstrably have.
  it("keeps the chat step when the flag is missing entirely (fail open, like the backend)", () => {
    const flags = { ...DEFAULT_FEATURE_FLAGS }
    delete (flags as Partial<typeof flags>).agents
    expect(idsFor(audience({ flags }))).toContain("ask")
  })

  it("withholds the chat step when the agents module is off", () => {
    // `agents` is ALL chat capability — the route 403s — so explaining the
    // composer would teach a feature that refuses the call.
    const ids = idsFor(audience({ flags: { ...DEFAULT_FEATURE_FLAGS, agents: false } }))
    expect(ids).not.toContain("ask")
  })

  it("still runs a real tour for a company with chat switched off", () => {
    // Two steps, both carrying content. `ProductTour`'s floor is 2 for exactly
    // this case: one step is a popup, two is still a tour. If that floor is
    // ever raised again, this company silently loses the tour entirely.
    const ids = idsFor(
      audience({
        flags: { ...DEFAULT_FEATURE_FLAGS, agents: false },
        orgRole: "member",
        onTrial: false,
      }),
    )
    expect(ids).toEqual(["welcome", "projects"])
  })

  it("gates nothing else — the remaining steps are true for every viewer", () => {
    // A member, off trial, one workspace, every optional module off. The
    // opener describes the product and Projects is not module-gated, so both
    // stand. This is the assertion that fails if someone adds a `when` to a
    // step that does not need one.
    const bare = audience({
      flags: { agents: false, top_insights: false } as TourAudience["flags"],
      orgRole: "member",
      onTrial: false,
      workspaceCount: 1,
    })
    expect(idsFor(bare)).toContain("welcome")
    expect(idsFor(bare)).toContain("projects")
  })
})

describe("what a step is allowed to say", () => {
  it("carries plain text only — a step can never inject markup", () => {
    // The body renders into a <p> as a text node, and it must stay that way:
    // this copy is ours, but the invariant is what keeps it safe if it ever
    // becomes configurable.
    for (const s of TOUR_STEPS) {
      expect(s.body, `${s.id} contains markup`).not.toMatch(/<[a-z/]/i)
      expect(s.title.trim().length).toBeGreaterThan(0)
      expect(s.body.trim().length).toBeGreaterThan(0)
    }
  })

  it("stays short enough to read in a card", () => {
    // A tour card is a paragraph, not a page — and the card is positioned by
    // measuring its own height, so a body that runs long is also the shape
    // that pushed Next off-screen once (see ProductTour's clamp test).
    for (const s of TOUR_STEPS) {
      expect(s.body.length, `${s.id} is too long for a tour card`).toBeLessThan(260)
    }
  })

  it("names an anchor that the app actually writes", () => {
    // DERIVED from the source, not a hand-kept list — a hardcoded set passes
    // happily while the attribute it names has been renamed or deleted, which
    // is the failure it was supposed to catch.
    //
    // WHAT THIS STILL CANNOT SEE: whether the element RENDERS. A missing
    // anchor centres the step rather than breaking it, which is why this
    // asserts "written", not "rendered".
    const root = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "..")
    const written = new Set<string>()
    const walk = (dir: string) => {
      for (const e of readdirSync(dir, { withFileTypes: true })) {
        if (e.name === "node_modules" || e.name === "__tests__") continue
        const full = join(dir, e.name)
        if (e.isDirectory()) walk(full)
        else if (e.name.endsWith(".tsx")) {
          const src = readFileSync(full, "utf8")
          for (const m of src.matchAll(/data-tour=\{?["'`]([^"'`{}$]+)["'`]/g)) {
            written.add(m[1])
          }
          // The rail derives its anchors from the screen id in a template
          // literal, so they are matched by shape rather than by text.
          if (/data-tour=\{`nav-\$\{/.test(src)) {
            for (const nav of ["brief", "artifacts", "projects", "backlog"]) {
              written.add(`nav-${nav}`)
            }
          }
        }
      }
    }
    walk(root)

    for (const s of TOUR_STEPS) {
      if (!s.anchor) continue
      expect(
        written.has(s.anchor),
        `step "${s.id}" points at data-tour="${s.anchor}", which nothing writes`,
      ).toBe(true)
    }
  })
})

describe("the tour is actually mounted", () => {
  // THE BUG THIS GUARDS, and it shipped once. Every other test here renders
  // <ProductTour /> directly, so the component was green while nothing in the
  // app rendered it — the edit that was supposed to mount it anchored on a
  // line that exists on a different branch, matched nothing, and said nothing.
  // tsc and the build both passed, because an unmounted module is merely
  // unused. Only opening the app would have caught it, and that is not a test.
  //
  // Asserted against the SOURCE rather than by rendering AppShell, which would
  // need six providers stood up to prove one line.
  it("AppShell imports and renders <ProductTour />", () => {
    const shell = readFileSync(
      join(dirname(fileURLToPath(import.meta.url)), "..", "..", "..", "(app)", "AppShell.tsx"),
      "utf8",
    )
    expect(shell, "AppShell does not import ProductTour").toMatch(
      /import\(\s*["'][^"']*components\/tour\/ProductTour["']\s*\)/,
    )
    expect(shell, "AppShell imports ProductTour but never renders it").toMatch(
      /<ProductTour\s*\/>/,
    )
  })

  it("does not suppress a two-step tour", () => {
    // The floor lives in ProductTour and the tour's shape lives here; a change
    // to either can silently switch the tour off for a company with chat
    // disabled. Asserted against the source, since the guard runs inside an
    // effect that needs a workspace, a profile and a role to reach.
    const tour = readFileSync(
      join(dirname(fileURLToPath(import.meta.url)), "..", "ProductTour.tsx"),
      "utf8",
    )
    expect(tour).toMatch(/resolved\.length\s*<\s*2/)
  })
})
