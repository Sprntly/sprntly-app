// @vitest-environment jsdom
//
// Container mount test for onboarding step 08 — "Create your workspace" (the
// FINAL step). Covers: the name input prefills from the workspace's product
// name; "Finish setup" names the real workspace row via
// onboardingApi.createWorkspace(name) then completeOnboarding + replace(/brief);
// a FAILING naming call is best-effort — the error notice shows but completion
// and the redirect still happen; "skip naming" completes without ever calling
// the naming endpoint. The first-brief kick (workspace-brief) is stubbed so
// the mount is offline and deterministic.
//
// Matchers: native DOM only (no @testing-library/jest-dom).
import * as React from "react"
import { act, cleanup, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const authMock = vi.fn()
const onboardingMock = vi.fn()
const routerMock = { push: vi.fn(), replace: vi.fn() }
const completeMock = vi.fn()
const setContentMock = vi.fn()
const onbCreateWorkspaceMock = vi.fn()
const ensureMock = vi.fn()
const seedMock = vi.fn()
const fetchBriefMock = vi.fn()
const startGenMock = vi.fn()

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
}))
// The brief-generation client runs from the finish handler (fire-and-forget);
// stub it so the mount is offline and deterministic.
vi.mock("../../../../lib/workspace-brief", () => ({
  ensureDatasetForWorkspace: (...a: unknown[]) => ensureMock(...a),
  seedWorkspaceContextFiles: (...a: unknown[]) => seedMock(...a),
  fetchBriefWhenReady: (...a: unknown[]) => fetchBriefMock(...a),
  startBriefGeneration: (...a: unknown[]) => startGenMock(...a),
}))
vi.mock("../../../../lib/brief-adapter", () => ({
  briefToContentPatch: vi.fn(() => ({ patched: true })),
}))
vi.mock("../../../../lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../../lib/api")>()
  return {
    ...actual,
    onboardingApi: {
      ...actual.onboardingApi,
      createWorkspace: (...a: unknown[]) => onbCreateWorkspaceMock(...a),
    },
  }
})

import { WorkspaceStep } from "../WorkspaceStep"
import { makeWorkspace, makeOnboardingCtx } from "./fixtures"

function makeProduct(over: Record<string, unknown> = {}) {
  return {
    id: "p-1",
    company_id: "ws-1",
    name: "Acme App",
    website: "https://acme.com",
    description: null,
    is_primary: true,
    surfaces: ["web"],
    personas: [],
    positioning: null,
    monetization: [],
    maturity: null,
    ...over,
  }
}

function mount(workspace = makeWorkspace({ onboarding_step: 8, product: makeProduct() })) {
  onboardingMock.mockReturnValue(makeOnboardingCtx({ workspace }))
  return render(React.createElement(WorkspaceStep))
}

function nameInput(): HTMLInputElement {
  return document.querySelector('input[aria-label="Workspace name"]') as HTMLInputElement
}

function finishBtn(): HTMLButtonElement {
  return Array.from(document.querySelectorAll("button")).find((b) =>
    /finish setup/i.test(b.textContent ?? ""),
  ) as HTMLButtonElement
}

beforeEach(() => {
  authMock.mockReturnValue({ kind: "authed", user: { id: "u-1" }, session: {} })
  // The fire-and-forget brief kick must never reject unhandled.
  ensureMock.mockResolvedValue(undefined)
  seedMock.mockResolvedValue(undefined)
  fetchBriefMock.mockResolvedValue(null)
  startGenMock.mockResolvedValue(undefined)
  completeMock.mockResolvedValue(undefined)
  window.localStorage.clear()
})
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("WorkspaceStep (onboarding step 08 — name the workspace + finish)", () => {
  it("prefills the workspace name from the workspace's product name", () => {
    mount()
    expect(nameInput()).not.toBeNull()
    expect(nameInput().value).toBe("Acme App")
  })

  it("falls back to the company display name when there is no product", () => {
    mount(makeWorkspace({ onboarding_step: 8, product: null }))
    expect(nameInput().value).toBe("Acme")
  })

  it("'Finish setup' names the workspace, completes onboarding and enters the app", async () => {
    onbCreateWorkspaceMock.mockResolvedValue({
      id: "w-1",
      name: "Acme App",
      slug: "acme-app",
      is_default: true,
    })
    mount()

    await act(async () => {
      finishBtn().click()
    })

    await waitFor(() => {
      expect(routerMock.replace).toHaveBeenCalledWith("/brief")
    })
    expect(onbCreateWorkspaceMock).toHaveBeenCalledWith("Acme App")
    expect(completeMock).toHaveBeenCalledWith("ws-1", "u-1")
    expect(window.localStorage.getItem("sprntly_active_company")).toBe("acme")
  })

  it("a FAILING naming call is best-effort: error shows, completion + redirect still happen", async () => {
    onbCreateWorkspaceMock.mockRejectedValue(new Error("boom"))
    mount()

    await act(async () => {
      finishBtn().click()
    })

    await waitFor(() => {
      expect(routerMock.replace).toHaveBeenCalledWith("/brief")
    })
    expect(onbCreateWorkspaceMock).toHaveBeenCalledWith("Acme App")
    expect(completeMock).toHaveBeenCalledWith("ws-1", "u-1")
    expect(
      screen.getByText(/Couldn't name your workspace just now/),
    ).not.toBeNull()
  })

  it("'skip naming' completes onboarding without calling the naming endpoint", async () => {
    mount()

    const skip = screen.getByText("skip naming") as HTMLButtonElement
    await act(async () => {
      skip.click()
    })

    await waitFor(() => {
      expect(routerMock.replace).toHaveBeenCalledWith("/brief")
    })
    expect(onbCreateWorkspaceMock).not.toHaveBeenCalled()
    expect(completeMock).toHaveBeenCalledWith("ws-1", "u-1")
  })

  it("shows the loading shell while the workspace is loading", () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx({ loading: true, workspace: null }))
    render(React.createElement(WorkspaceStep))
    expect(screen.getByText("Loading…")).not.toBeNull()
  })

  it("redirects to step 1 from an EFFECT (never during render) when there is no workspace", () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx({ workspace: null }))

    const errors: unknown[] = []
    const spy = vi
      .spyOn(console, "error")
      .mockImplementation((...args) => errors.push(args[0]))
    render(React.createElement(WorkspaceStep))
    spy.mockRestore()

    expect(routerMock.replace).toHaveBeenCalledWith("/onboarding/company")
    expect(screen.getByText("Loading…")).not.toBeNull()
    const sideEffectInRender = errors
      .map(String)
      .filter((m) => /while rendering a different component|Cannot update a component/.test(m))
    expect(sideEffectInRender).toEqual([])
  })
})
