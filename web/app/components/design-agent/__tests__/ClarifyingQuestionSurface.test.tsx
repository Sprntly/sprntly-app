// P3-16 — ClarifyingQuestionSurface tests. Node-env vitest (no DOM, no
// testing-library), so — following the repo's renderToStaticMarkup convention —
// pure views are SSR-rendered for markup assertions. For the load-bearing
// "answer-routes-as-iterate" invariant (AC3) we DRIVE THE REAL CONTAINER HANDLER
// (a choice click) against a spy on the REAL designAgentApi.iterate, so the AC
// is genuinely locked: a future edit that stopped routing through P3-14's
// iterate would fail this test. The free-text path's composition + single-call
// invariant is covered via the exported pure `runAnswer` helper (the container
// reads the typed answer from useState, which node-env's server renderer cannot
// mutate — the choice path needs no state, so it drives cleanly).
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { readFileSync, readdirSync } from "node:fs"
import { join } from "node:path"
import { afterEach, describe, expect, it, vi } from "vitest"

// Sprntly components carry no `import React`; the classic JSX runtime reads
// `globalThis.React`, so expose it (repo-wide test convention).
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import {
  ClarifyingQuestionSurface,
  ClarifyingQuestionSurfaceView,
  composeAnswerPrompt,
  shouldRenderSurface,
  runAnswer,
  type IterateFn,
} from "../ClarifyingQuestionSurface"
import { designAgentApi } from "../../../lib/api"
import type {
  IterateResponse,
  PendingQuestion,
  PrototypeRecord,
} from "../../../lib/api"

afterEach(() => {
  vi.restoreAllMocks()
})

const GEN_RESP: IterateResponse = {
  prototype_id: 7,
  status: "generating",
  queue_position: 0,
}

function prototype(overrides: Partial<PrototypeRecord> = {}): PrototypeRecord {
  return {
    id: 7,
    status: "ready",
    bundle_url: null,
    error: null,
    is_complete: false,
    share_mode: "private",
    share_token: null,
    pending_question: null,
    ...overrides,
  }
}

function question(overrides: Partial<PendingQuestion> = {}): PendingQuestion {
  return {
    question: "Should the dashboard default to a list or a grid?",
    ...overrides,
  }
}

function renderView(
  props: React.ComponentProps<typeof ClarifyingQuestionSurfaceView>,
): string {
  return renderToStaticMarkup(
    React.createElement(ClarifyingQuestionSurfaceView, props),
  )
}

/**
 * Render the REAL ClarifyingQuestionSurface container and return the props it
 * passes to its View — including the live handler closures (`onChoose`,
 * `onSubmit`, `onAnswerChange`). Wraps the classic JSX factory on
 * `globalThis.React` (the factory the component reads) so we capture the View
 * props without mocking the same-module View. useState setters fired by those
 * handlers post-render are no-ops in the server renderer (the choice path needs
 * no state, so its routing is fully observable). Returns null when the container
 * rendered nothing (gated off).
 */
function driveContainer(
  props: React.ComponentProps<typeof ClarifyingQuestionSurface>,
): React.ComponentProps<typeof ClarifyingQuestionSurfaceView> | null {
  const realReact = (globalThis as { React?: typeof React }).React!
  const realCreate = realReact.createElement
  const calls: Array<[unknown, Record<string, unknown> | null]> = []
  ;(globalThis as { React?: unknown }).React = {
    ...realReact,
    createElement: (
      type: unknown,
      p: Record<string, unknown> | null,
      ...kids: unknown[]
    ) => {
      calls.push([type, p])
      return (realCreate as (...a: unknown[]) => unknown)(type, p, ...kids)
    },
  }
  try {
    renderToStaticMarkup(
      (realCreate as (...a: unknown[]) => React.ReactElement)(
        ClarifyingQuestionSurface,
        props,
      ),
    )
  } finally {
    ;(globalThis as { React?: unknown }).React = realReact
  }
  const call = calls.find((c) => c[0] === ClarifyingQuestionSurfaceView)
  return call
    ? (call[1] as React.ComponentProps<typeof ClarifyingQuestionSurfaceView>)
    : null
}

// ---- pure helpers -----------------------------------------------------------

describe("composeAnswerPrompt — prepends the question as context", () => {
  it("incorporates both the original question and the answer", () => {
    const out = composeAnswerPrompt("List or grid?", "  Grid  ")
    expect(out).toContain("List or grid?")
    expect(out).toContain("Grid")
    // trims the answer
    expect(out).not.toContain("  Grid  ")
  })
})

describe("shouldRenderSurface — gating", () => {
  it("false when no pending_question", () => {
    expect(shouldRenderSurface(prototype({ pending_question: null }))).toBe(false)
  })
  it("true when a question is pending and not locked", () => {
    expect(
      shouldRenderSurface(prototype({ pending_question: question() })),
    ).toBe(true)
  })
  it("false when locked (F14) even with a pending question", () => {
    expect(
      shouldRenderSurface(
        prototype({ pending_question: question(), is_complete: true }),
      ),
    ).toBe(false)
  })
})

describe("runAnswer — routes the answer as a single iterate", () => {
  it("calls iterate once with the composed prompt + mode:'execute'", async () => {
    const iterate = vi.fn<IterateFn>().mockResolvedValue(GEN_RESP)
    await runAnswer(iterate, {
      prototypeId: 7,
      question: "List or grid?",
      answer: "Grid",
    })
    expect(iterate).toHaveBeenCalledTimes(1)
    const [id, body] = iterate.mock.calls[0]
    expect(id).toBe(7)
    expect(body.mode).toBe("execute")
    expect(body.prompt).toContain("List or grid?")
    expect(body.prompt).toContain("Grid")
  })
})

// ---- AC1: render / null gating ----------------------------------------------

describe("AC1 — renders the question (+ context) or nothing", () => {
  it("test_renders_nothing_when_no_pending_question", () => {
    const viewProps = driveContainer({
      prototype: prototype({ pending_question: null }),
    })
    expect(viewProps).toBeNull() // container returned null → no View mounted
  })

  it("test_renders_question_and_context", () => {
    const html = renderView({
      question: "Should the dashboard default to a list or a grid?",
      context: "The PRD mentions both in different sections.",
      choices: null,
      answer: "",
    })
    expect(html).toContain('data-testid="clarifying-question-text"')
    expect(html).toContain("Should the dashboard default to a list or a grid?")
    expect(html).toContain('data-testid="clarifying-question-context"')
    expect(html).toContain("The PRD mentions both in different sections.")
  })

  it("omits the context line when no context is present", () => {
    const html = renderView({
      question: "List or grid?",
      context: null,
      choices: null,
      answer: "",
    })
    expect(html).not.toContain('data-testid="clarifying-question-context"')
  })
})

// ---- AC2: choices vs free-text ----------------------------------------------

describe("AC2 — choices render as buttons; otherwise free text", () => {
  it("test_renders_choices_as_buttons_when_present", () => {
    const html = renderView({
      question: "List or grid?",
      choices: ["List", "Grid"],
      answer: "",
    })
    expect(html).toContain('data-testid="clarifying-question-choices"')
    // both choices rendered as buttons; no free-text input
    expect(html).toContain("List")
    expect(html).toContain("Grid")
    expect(html).not.toContain('data-testid="clarifying-question-input"')
  })

  it("test_renders_free_text_input_when_no_choices", () => {
    const html = renderView({
      question: "What tone should the copy take?",
      choices: null,
      answer: "",
    })
    expect(html).toContain('data-testid="clarifying-question-input"')
    expect(html).toContain('data-testid="clarifying-question-submit"')
    expect(html).not.toContain('data-testid="clarifying-question-choices"')
  })

  it("treats an empty choices array as free-text (no empty button row)", () => {
    const html = renderView({
      question: "What tone?",
      choices: [],
      answer: "",
    })
    expect(html).toContain('data-testid="clarifying-question-input"')
    expect(html).not.toContain('data-testid="clarifying-question-choices"')
  })
})

// ---- AC3: answer routes as iterate (driven through the REAL container) -------

describe("AC3 — submitting an answer routes through P3-14's iterate exactly once", () => {
  it("test_submit_routes_answer_as_iterate (choice click → designAgentApi.iterate)", async () => {
    const iter = vi.spyOn(designAgentApi, "iterate").mockResolvedValue(GEN_RESP)

    // Default iterate (no injection) → proves the production path uses P3-14's
    // method (no new api method introduced).
    const viewProps = driveContainer({
      prototype: prototype({
        pending_question: question({
          question: "List or grid?",
          choices: ["List", "Grid"],
        }),
      }),
    })
    expect(viewProps).not.toBeNull()
    expect(typeof viewProps!.onChoose).toBe("function")

    await viewProps!.onChoose!("Grid")

    expect(iter).toHaveBeenCalledTimes(1)
    const [id, body] = iter.mock.calls[0]
    expect(id).toBe(7)
    expect(body.mode).toBe("execute")
    expect(body.prompt).toContain("List or grid?") // question as context
    expect(body.prompt).toContain("Grid") // the chosen answer
  })

  it("does not fire iterate on an empty free-text submit", async () => {
    const iter = vi.spyOn(designAgentApi, "iterate").mockResolvedValue(GEN_RESP)
    const viewProps = driveContainer({
      prototype: prototype({ pending_question: question({ choices: undefined }) }),
    })
    // free-text mode, answer empty → onSubmit is a no-op (button is disabled too)
    await viewProps!.onSubmit!()
    expect(iter).not.toHaveBeenCalled()
  })
})

// ---- AC4: clears after submit; no self-poll / no own progress ----------------

describe("AC4 — clears optimistically and does not self-poll", () => {
  it("renders no progress / status / spinner UI of its own", () => {
    const html = renderView({
      question: "List or grid?",
      choices: ["List", "Grid"],
      answer: "",
    })
    expect(html).not.toContain("progress")
    expect(html).not.toContain("spinner")
    expect(html).not.toContain('data-testid="clarifying-question-poll"')
  })

  it("test_surface_clears_after_submit_and_does_not_self_poll (source guard)", () => {
    // The surface must not start a timer or re-fetch the prototype itself — it
    // clears its LOCAL copy and hands off to the launcher's existing poll. Cheap
    // static guard (mirrors the external-exclusion check): the component source
    // references no polling primitive.
    const src = readFileSync(
      join(process.cwd(), "app", "components", "design-agent", "ClarifyingQuestionSurface.tsx"),
      "utf8",
    )
    expect(src).not.toContain("setInterval")
    expect(src).not.toContain("setTimeout")
    expect(src).not.toContain("designAgentApi.get")
    expect(src).not.toContain(".get(") // no GET poll of any kind
  })
})

// ---- AC5: locked gating (F14) -----------------------------------------------

describe("AC5 — locked prototype hides the surface", () => {
  it("test_locked_prototype_hides_surface", async () => {
    const iter = vi.spyOn(designAgentApi, "iterate").mockResolvedValue(GEN_RESP)
    const viewProps = driveContainer({
      prototype: prototype({
        pending_question: question({ choices: ["List", "Grid"] }),
        is_complete: true,
      }),
    })
    // container returns null when locked → no View, no answer can fire
    expect(viewProps).toBeNull()
    expect(iter).not.toHaveBeenCalled()
  })
})

// ---- AC6: external-viewer exclusion -----------------------------------------

describe("AC6 — external-viewer exclusion (F12 internal-only)", () => {
  it("test_public_token_page_does_not_mount_clarifying_question_surface", () => {
    // vitest runs from web/ (cwd). Every public share depth (legacy, 2-seg,
    // 3-seg) resolves through one catch-all route, so its source files live
    // across app/p/ and its [...segments] subtree — walk the whole subtree
    // (excluding tests).
    const root = join(process.cwd(), "app", "p")
    function walk(dir: string): string[] {
      const out: string[] = []
      for (const entry of readdirSync(dir, { withFileTypes: true })) {
        if (entry.name === "__tests__") continue
        const full = join(dir, entry.name)
        if (entry.isDirectory()) out.push(...walk(full))
        else if (entry.name.endsWith(".ts") || entry.name.endsWith(".tsx"))
          out.push(full)
      }
      return out
    }
    const files = walk(root)
    // sanity: the walk actually found real files (the catch-all shell exists).
    expect(files.some((f) => f.endsWith(join("[...segments]", "page.tsx")))).toBe(true)
    for (const f of files) {
      const src = readFileSync(f, "utf8")
      expect(src).not.toContain("ClarifyingQuestionSurface")
    }
  })
})

// ---- AC7: never UI-dead-ended -----------------------------------------------

describe("AC7 — an awaiting_clarification prototype is never UI-dead-ended", () => {
  it("test_awaiting_clarification_is_not_ui_dead_ended (choices → buttons present)", () => {
    const viewProps = driveContainer({
      prototype: prototype({
        pending_question: question({ choices: ["List", "Grid"] }),
      }),
    })
    expect(viewProps).not.toBeNull()
    const html = renderView(viewProps!)
    // an answer affordance is present (choice buttons)
    expect(html).toContain('data-testid="clarifying-question-choice"')
  })

  it("free-text question still presents an answer affordance (input + submit)", () => {
    const viewProps = driveContainer({
      prototype: prototype({
        pending_question: question({ choices: undefined }),
      }),
    })
    expect(viewProps).not.toBeNull()
    const html = renderView(viewProps!)
    expect(html).toContain('data-testid="clarifying-question-input"')
    expect(html).toContain('data-testid="clarifying-question-submit"')
  })
})

// ---- branding: internal persona name never reaches the user ----------------

describe("branding — Sprntly, not the internal persona name", () => {
  it("test_region_aria_label_says_sprntly_not_design_agent", () => {
    const html = renderView({
      question: "List or grid?",
      choices: null,
      answer: "",
    })
    expect(html).toContain('aria-label="Sprntly has a question"')
    expect(html).not.toContain("Design Agent")
  })

  it("test_free_text_placeholder_says_sprntly_not_design_agent", () => {
    const html = renderView({
      question: "What tone should the copy take?",
      choices: null,
      answer: "",
    })
    expect(html).toContain('placeholder="Answer Sprntly…"')
    expect(html).not.toContain("Design Agent")
  })

  it("test_clarifying_question_surface_source_has_no_design_agent_string", () => {
    const src = readFileSync(
      join(process.cwd(), "app", "components", "design-agent", "ClarifyingQuestionSurface.tsx"),
      "utf8",
    )
    expect(src).not.toContain("Design Agent")
  })
})

// ---- error handling ---------------------------------------------------------

describe("error handling", () => {
  it("renders the error line when the view is given an error", () => {
    const html = renderView({
      question: "List or grid?",
      choices: null,
      answer: "Grid",
      error: "Could not submit your answer",
    })
    expect(html).toContain('data-testid="clarifying-question-error"')
    expect(html).toContain("Could not submit your answer")
  })
})
