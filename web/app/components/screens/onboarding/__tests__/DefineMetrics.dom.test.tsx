// @vitest-environment jsdom
//
// Container mount test for the post-wizard define-metrics sub-flow (v6
// screenshot spec 2026-07-17) — the TRANSIENT, UNNUMBERED closer at
// /onboarding/define-metrics. One screen per metric picked in step 3
// (AI-drafted definition + analytics mapping, both editable), then a review
// table (metric / mapping / baseline with "—" fallback), and "Looks right ·
// generate knowledge graph" persists companies.metric_definitions, COMPLETES
// onboarding, kicks the first brief and enters the app at /brief.
//
// Covers: drafts requested from onboardingApi.draftMetricDefinitions (unless
// definitions are already saved on the company); per-metric confirm walks
// through to the review table; finish persists + completes + kicks the brief
// pipeline (mocked lib/workspace-brief); a failed draft falls back to blank,
// hand-written definitions.
//
// Matchers: native DOM only.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const authMock = vi.fn()
const onboardingMock = vi.fn()
const routerMock = { push: vi.fn(), replace: vi.fn() }
const setContentMock = vi.fn()
const saveDefsMock = vi.fn()
const completeMock = vi.fn()
const draftDefsMock = vi.fn()
const ensureDatasetMock = vi.fn()
const seedContextMock = vi.fn()
const fetchBriefMock = vi.fn()
const startBriefMock = vi.fn()

vi.mock("../../../../lib/auth", () => ({ useAuth: () => authMock() }))
vi.mock("../../../../context/OnboardingContext", () => ({
  useOnboarding: () => onboardingMock(),
}))
vi.mock("../../../../context/ContentContext", () => ({
  useContent: () => ({ setContent: setContentMock }),
}))
vi.mock("next/navigation", () => ({ useRouter: () => routerMock }))
vi.mock("../../../../lib/onboarding/store", () => ({
  completeOnboarding: (...a: unknown[]) => completeMock(...a),
  saveMetricDefinitions: (...a: unknown[]) => saveDefsMock(...a),
}))
vi.mock("../../../../lib/api", () => ({
  onboardingApi: { draftMetricDefinitions: (...a: unknown[]) => draftDefsMock(...a) },
}))
vi.mock("../../../../lib/brief-adapter", () => ({
  briefToContentPatch: (b: unknown) => ({ patched: b }),
}))
vi.mock("../../../../lib/workspace-brief", () => ({
  ensureDatasetForWorkspace: (...a: unknown[]) => ensureDatasetMock(...a),
  seedWorkspaceContextFiles: (...a: unknown[]) => seedContextMock(...a),
  fetchBriefWhenReady: (...a: unknown[]) => fetchBriefMock(...a),
  startBriefGeneration: (...a: unknown[]) => startBriefMock(...a),
}))

import { DefineMetrics } from "../DefineMetrics"
import { makeWorkspace, makeOnboardingCtx } from "./fixtures"

const TWO_METRIC_TREE = {
  north_star: "Activation rate",
  north_star_description: "",
  metrics: [
    { name: "Activation rate", description: "" },
    { name: "Retention", description: "" },
  ],
}

const DRAFTED = [
  {
    metric: "Activation rate",
    definition: "Signups that finish setup within 7 days.",
    mapping: "event: setup_complete",
    baseline: "38%",
  },
  {
    metric: "Retention",
    definition: "Users active again in week 4.",
    mapping: "event: session_start",
    baseline: null,
  },
]

function mount(workspace = makeWorkspace({ onboarding_step: 9, kpi_tree: TWO_METRIC_TREE })) {
  onboardingMock.mockReturnValue(makeOnboardingCtx({ workspace }))
  return render(React.createElement(DefineMetrics))
}

function confirmBtn(): HTMLButtonElement {
  return Array.from(document.querySelectorAll("button")).find((b) =>
    /^Confirm ·/.test((b.textContent ?? "").trim()),
  ) as HTMLButtonElement
}

function finishBtn(): HTMLButtonElement {
  return Array.from(document.querySelectorAll("button")).find((b) =>
    /Looks right · generate knowledge graph/.test(b.textContent ?? ""),
  ) as HTMLButtonElement
}

beforeEach(() => {
  authMock.mockReturnValue({ kind: "authed", user: { id: "u-1" }, session: {} })
  ensureDatasetMock.mockResolvedValue(undefined)
  seedContextMock.mockResolvedValue(undefined)
  fetchBriefMock.mockResolvedValue(null)
  startBriefMock.mockResolvedValue(undefined)
  window.localStorage.clear()
})
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("DefineMetrics (unnumbered define-metrics sub-flow)", () => {
  it("drafts definitions for the picked metrics and walks one screen per metric to the review table", async () => {
    draftDefsMock.mockResolvedValue({ definitions: DRAFTED })
    mount()

    // Drafting state first, no progress dots (unnumbered, like your-name).
    expect(screen.getByText(/Drafting how Sprntly should measure/)).not.toBeNull()
    expect(document.querySelector(".onb-dots")).toBeNull()

    await waitFor(() => {
      expect(screen.getByText("Metric 1 of 2")).not.toBeNull()
    })
    expect(draftDefsMock).toHaveBeenCalledWith(["Activation rate", "Retention"])

    // Metric 1 pre-filled from the draft, both fields editable.
    const def1 = screen.getByLabelText("Activation rate definition") as HTMLTextAreaElement
    expect(def1.value).toBe("Signups that finish setup within 7 days.")
    const map1 = screen.getByLabelText("Activation rate analytics mapping") as HTMLInputElement
    expect(map1.value).toBe("event: setup_complete")
    fireEvent.change(map1, { target: { value: "event: onboarding_done" } })

    // Confirm → metric 2, whose button offers the review handoff.
    expect(confirmBtn().textContent).toMatch(/Confirm · next metric/)
    fireEvent.click(confirmBtn())
    expect(screen.getByText("Metric 2 of 2")).not.toBeNull()
    expect(
      (screen.getByLabelText("Retention definition") as HTMLTextAreaElement).value,
    ).toBe("Users active again in week 4.")
    expect(confirmBtn().textContent).toMatch(/Confirm · review/)

    // Review table: metric / mapping / baseline ("—" when null).
    fireEvent.click(confirmBtn())
    expect(screen.getByText(/look right\?/)).not.toBeNull()
    const rows = Array.from(document.querySelectorAll(".onb-review-table tr"))
    expect(rows.length).toBe(2)
    expect(rows[0].textContent).toContain("Activation rate")
    expect(rows[0].textContent).toContain("event: onboarding_done")
    expect(rows[0].textContent).toContain("38%")
    expect(rows[1].textContent).toContain("Retention")
    expect(rows[1].textContent).toContain("—")
  })

  it("finish persists the definitions, completes onboarding, kicks the first brief and enters the app", async () => {
    draftDefsMock.mockResolvedValue({ definitions: DRAFTED })
    saveDefsMock.mockResolvedValue(makeWorkspace())
    completeMock.mockResolvedValue(undefined)
    mount()

    await waitFor(() => {
      expect(screen.getByText("Metric 1 of 2")).not.toBeNull()
    })
    fireEvent.click(confirmBtn())
    fireEvent.click(confirmBtn())

    await act(async () => {
      finishBtn().click()
    })

    await waitFor(() => {
      expect(routerMock.replace).toHaveBeenCalledWith("/brief")
    })
    // Definitions persisted as confirmed (metric/definition/mapping/baseline).
    expect(saveDefsMock).toHaveBeenCalledTimes(1)
    expect(saveDefsMock.mock.calls[0][0]).toBe("ws-1")
    expect(saveDefsMock.mock.calls[0][1]).toEqual([
      {
        metric: "Activation rate",
        definition: "Signups that finish setup within 7 days.",
        mapping: "event: setup_complete",
        baseline: "38%",
      },
      {
        metric: "Retention",
        definition: "Users active again in week 4.",
        mapping: "event: session_start",
        baseline: null,
      },
    ])
    expect(completeMock).toHaveBeenCalledWith("ws-1", "u-1")
    expect(window.localStorage.getItem("sprntly_active_company")).toBe("acme")
    // The first-brief kick ran through the workspace-brief pipeline.
    await waitFor(() => {
      expect(ensureDatasetMock).toHaveBeenCalledTimes(1)
      expect(seedContextMock).toHaveBeenCalledTimes(1)
      expect(fetchBriefMock).toHaveBeenCalledWith("acme")
      // No brief ready yet → generation is started.
      expect(startBriefMock).toHaveBeenCalledWith("acme")
    })
  })

  it("uses saved metric_definitions instead of drafting when the company already has them", async () => {
    draftDefsMock.mockResolvedValue({ definitions: DRAFTED })
    mount(
      makeWorkspace({
        onboarding_step: 9,
        kpi_tree: TWO_METRIC_TREE,
        metric_definitions: [
          {
            metric: "Activation rate",
            definition: "Saved definition.",
            mapping: "event: saved",
            baseline: null,
          },
          {
            metric: "Retention",
            definition: "Saved too.",
            mapping: "",
            baseline: null,
          },
        ],
      }),
    )

    await waitFor(() => {
      expect(screen.getByText("Metric 1 of 2")).not.toBeNull()
    })
    expect(draftDefsMock).not.toHaveBeenCalled()
    expect(
      (screen.getByLabelText("Activation rate definition") as HTMLTextAreaElement).value,
    ).toBe("Saved definition.")
  })

  it("a failed draft falls back to blank, hand-written definitions", async () => {
    draftDefsMock.mockRejectedValue(new Error("llm down"))
    mount()

    await waitFor(() => {
      expect(screen.getByText("Metric 1 of 2")).not.toBeNull()
    })
    expect(
      (screen.getByLabelText("Activation rate definition") as HTMLTextAreaElement).value,
    ).toBe("")
  })

  it("with no picked metrics it goes straight to review (nothing to define) and never drafts", async () => {
    draftDefsMock.mockResolvedValue({ definitions: [] })
    mount(makeWorkspace({ onboarding_step: 9 }))

    await waitFor(() => {
      expect(screen.getByText(/haven't picked metrics yet/)).not.toBeNull()
    })
    expect(draftDefsMock).not.toHaveBeenCalled()
  })

  it("shows the loading shell while the workspace is loading", () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx({ loading: true, workspace: null }))
    render(React.createElement(DefineMetrics))
    expect(screen.getByText("Loading…")).not.toBeNull()
  })

  it("redirects to step 1 from an EFFECT (never during render) when there is no workspace", () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx({ workspace: null }))

    const errors: unknown[] = []
    const spy = vi
      .spyOn(console, "error")
      .mockImplementation((...args) => errors.push(args[0]))
    render(React.createElement(DefineMetrics))
    spy.mockRestore()

    expect(routerMock.replace).toHaveBeenCalledWith("/onboarding/company")
    expect(screen.getByText("Loading…")).not.toBeNull()
    const sideEffectInRender = errors
      .map(String)
      .filter((m) => /while rendering a different component|Cannot update a component/.test(m))
    expect(sideEffectInRender).toEqual([])
  })
})
