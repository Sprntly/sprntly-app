// @vitest-environment jsdom
//
// Container mount test for onboarding step 06 — "Your workspace" (v7
// screenshot spec 2026-07-21). This step COLLAPSES the three former steps
// (team 06 / strategy 07 / decisions 08) into one card, so these tests stand in
// for the three deleted per-step suites.
//
// Covers: name* + scope* are required (error, no persistence, no navigation);
// strategy and roadmap are ONE field that persists to team_strategy and uploads
// through roadmapDocApi; sizing + "anything else" sit behind
// the "Add more" disclosure and persist to the pre-existing
// companies.sizing_methodology / additional_context columns; a valid Continue
// writes all of it plus onboarding_step 6 and routes to /onboarding/product.
//
// Matchers: native DOM only (no @testing-library/jest-dom).
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const authMock = vi.fn()
const onboardingMock = vi.fn()
const routerMock = { push: vi.fn(), replace: vi.fn() }
const updateWorkspaceMock = vi.fn()
const companyDocUploadMock = vi.fn()
const roadmapUploadMock = vi.fn()

vi.mock("../../../../lib/auth", () => ({ useAuth: () => authMock() }))
vi.mock("../../../../context/OnboardingContext", () => ({
  useOnboarding: () => onboardingMock(),
}))
vi.mock("next/navigation", () => ({ useRouter: () => routerMock }))
vi.mock("../../../../lib/onboarding/store", () => ({
  updateWorkspace: (...a: unknown[]) => updateWorkspaceMock(...a),
}))
vi.mock("../../../../lib/api", () => ({
  companyDocsApi: { upload: (...a: unknown[]) => companyDocUploadMock(...a) },
  roadmapDocApi: { upload: (...a: unknown[]) => roadmapUploadMock(...a) },
}))

import { WorkspaceStep } from "../WorkspaceStep"
import { makeWorkspace, makeOnboardingCtx } from "./fixtures"

function mount(workspace = makeWorkspace({ onboarding_step: 5 })) {
  authMock.mockReturnValue({ kind: "authed", user: { id: "u-1" }, session: {} })
  onboardingMock.mockReturnValue(makeOnboardingCtx({ workspace }))
  updateWorkspaceMock.mockResolvedValue(makeWorkspace({ onboarding_step: 6 }))
  companyDocUploadMock.mockResolvedValue({ ok: true })
  roadmapUploadMock.mockResolvedValue({ ok: true })
  return render(React.createElement(WorkspaceStep))
}

const nameInput = () =>
  document.querySelector('[data-field="teamName"] input') as HTMLInputElement
const scopeInput = () =>
  document.querySelector('[data-field="teamScope"] textarea') as HTMLTextAreaElement
const continueBtn = () =>
  Array.from(document.querySelectorAll(".onb-footer button")).find((b) =>
    /Next/.test(b.textContent ?? ""),
  ) as HTMLButtonElement

function openAddMore() {
  // "Add more" prefix is the stable handle; the suffix is editable copy.
  fireEvent.click(screen.getByText(/Add more/))
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("WorkspaceStep (onboarding step 06 — merged team/strategy/decisions)", () => {
  it("renders on step 5 with name* + scope* visible and the extras behind a disclosure", () => {
    const { container } = mount()
    expect(
      (container.querySelector(".onb-dots") as HTMLElement).getAttribute("data-step"),
    ).toBe("5")
    expect(
      (container.querySelector(".onb-card .onb-h") as HTMLElement).textContent,
    ).toBe("Your workspace.")
    expect(nameInput()).not.toBeNull()
    expect(scopeInput()).not.toBeNull()
    // Both required.
    expect(
      (document.querySelector('[data-field="teamName"]') as HTMLElement).querySelector(
        ".req",
      ),
    ).not.toBeNull()
    expect(
      (document.querySelector('[data-field="teamScope"]') as HTMLElement).querySelector(
        ".req",
      ),
    ).not.toBeNull()
    // Strategy and roadmap are ONE block.
    expect(document.querySelector('[data-field="team-strategy"]')).not.toBeNull()
    expect(document.querySelector('[data-field="team-roadmap"]')).toBeNull()
    // Sizing + anything-else are collapsed.
    expect(document.querySelector('[data-field="sizingMethodology"]')).toBeNull()
    expect(screen.getByText(/Add more/)).not.toBeNull()
  })

  it("Continue with an empty name or scope errors and does NOT persist or navigate", async () => {
    mount()
    await act(async () => {
      continueBtn().click()
    })
    expect(updateWorkspaceMock).not.toHaveBeenCalled()
    expect(routerMock.push).not.toHaveBeenCalled()

    // Name alone isn't enough — scope is required too.
    fireEvent.change(nameInput(), { target: { value: "Nutrition & Sleep" } })
    await act(async () => {
      continueBtn().click()
    })
    expect(updateWorkspaceMock).not.toHaveBeenCalled()
    expect(routerMock.push).not.toHaveBeenCalled()
  })

  it("a valid Continue persists every merged field and routes to product", async () => {
    mount()
    fireEvent.change(nameInput(), { target: { value: "Nutrition & Sleep" } })
    fireEvent.change(scopeInput(), {
      target: { value: "Owns food logging and sleep tracking end to end." },
    })
    openAddMore()
    fireEvent.change(
      document.querySelector('[data-field="sizingMethodology"] textarea') as HTMLTextAreaElement,
      { target: { value: "Fibonacci points, sized by the whole squad." } },
    )
    fireEvent.change(
      document.querySelector('[data-field="additionalContext"] textarea') as HTMLTextAreaElement,
      { target: { value: "We call the pairing flow 'sleep sync' internally." } },
    )

    await act(async () => {
      continueBtn().click()
    })

    await waitFor(() => {
      expect(routerMock.push).toHaveBeenCalledWith("/onboarding/product")
    })
    expect(updateWorkspaceMock).toHaveBeenCalledWith("ws-1", {
      team_name: "Nutrition & Sleep",
      team_scope: "Owns food logging and sleep tracking end to end.",
      team_strategy: null,
      // Retired from onboarding — the merged field absorbed it on seed.
      team_roadmap: null,
      // Reuses the column Settings → Process already owns — NOT a new one.
      sizing_methodology: "Fibonacci points, sized by the whole squad.",
      additional_context: "We call the pairing flow 'sleep sync' internally.",
      onboarding_step: 6,
    })
  })

  it("merges an existing roadmap column into the one strategy field and saves it there", async () => {
    mount(
      makeWorkspace({
        onboarding_step: 5,
        team_name: "Nutrition & Sleep",
        team_scope: "Owns food logging and sleep tracking end to end.",
        team_strategy: "Win the daily-habit loop this half.",
        team_roadmap: "Q3: sleep sync. Q4: calorie deficit v2.",
      }),
    )
    const typed = await waitFor(() => {
      const el = document.querySelector(
        '[data-field="team-strategy"] textarea',
      ) as HTMLTextAreaElement
      expect(el).not.toBeNull()
      return el
    })
    expect(typed.value).toBe(
      "Win the daily-habit loop this half.\n\nQ3: sleep sync. Q4: calorie deficit v2.",
    )

    await act(async () => {
      continueBtn().click()
    })
    await waitFor(() => {
      expect(updateWorkspaceMock).toHaveBeenCalled()
    })
    const payload = updateWorkspaceMock.mock.calls[0][1] as Record<string, unknown>
    expect(payload.team_strategy).toBe(
      "Win the daily-habit loop this half.\n\nQ3: sleep sync. Q4: calorie deficit v2.",
    )
    expect(payload.team_roadmap).toBeNull()
  })

  it("routes the merged strategy/roadmap upload through the roadmap-doc endpoint", async () => {
    mount()
    const input = document.querySelector(
      '[data-field="team-strategy"] input[type=file]',
    ) as HTMLInputElement
    fireEvent.change(input, {
      target: { files: [new File(["x"], "roadmap.pdf", { type: "application/pdf" })] },
    })
    await waitFor(() => {
      expect(roadmapUploadMock).toHaveBeenCalled()
    })
    expect(companyDocUploadMock).not.toHaveBeenCalled()
  })

  it("routes the sizing attachment through the sizing_doc doc type", async () => {
    mount()
    openAddMore()
    const input = document.querySelector(
      '[data-field="sizingMethodology"] input[type=file]',
    ) as HTMLInputElement
    fireEvent.change(input, {
      target: { files: [new File(["x"], "sizing.pdf", { type: "application/pdf" })] },
    })
    await waitFor(() => {
      expect(companyDocUploadMock).toHaveBeenCalled()
    })
    expect(companyDocUploadMock.mock.calls[0][1]).toBe("sizing_doc")
  })

  it("Back routes to the api-key step", () => {
    mount()
    fireEvent.click(screen.getByText("Back").closest("button") as HTMLElement)
    expect(routerMock.push).toHaveBeenCalledWith("/onboarding/api-key")
  })
})
