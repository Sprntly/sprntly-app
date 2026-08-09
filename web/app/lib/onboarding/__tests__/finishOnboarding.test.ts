// @vitest-environment node
//
// The onboarding closer must only build the first brief when a real data source
// is connected. Without one, seeding the onboarding context + generating would
// produce a brief from onboarding info alone — which we deliberately avoid (the
// brief appears once a data source is connected). See finishOnboarding.ts.
import { describe, it, expect, vi, beforeEach } from "vitest"

const completeOnboarding = vi.fn()
const ensureDatasetForWorkspace = vi.fn()
const seedWorkspaceContextFiles = vi.fn()
const fetchBriefWhenReady = vi.fn()
const startBriefGeneration = vi.fn()

vi.mock("../store", () => ({
  completeOnboarding: (...a: unknown[]) => completeOnboarding(...a),
}))
vi.mock("../../brief-adapter", () => ({
  briefToContentPatch: (b: unknown) => ({ patched: b }),
}))
vi.mock("../../workspace-brief", () => ({
  ensureDatasetForWorkspace: (...a: unknown[]) => ensureDatasetForWorkspace(...a),
  seedWorkspaceContextFiles: (...a: unknown[]) => seedWorkspaceContextFiles(...a),
  fetchBriefWhenReady: (...a: unknown[]) => fetchBriefWhenReady(...a),
  startBriefGeneration: (...a: unknown[]) => startBriefGeneration(...a),
}))

import { finishOnboardingAndEnterApp } from "../finishOnboarding"

const workspace = { id: "ws1", slug: "acme", product: {} } as unknown as Parameters<
  typeof finishOnboardingAndEnterApp
>[0]

// The brief work is fire-and-forget (a detached async IIFE); flush the
// microtask/timer queue so its awaited calls have run before we assert.
const flush = () => new Promise((r) => setTimeout(r, 0))

beforeEach(() => {
  for (const m of [
    completeOnboarding,
    ensureDatasetForWorkspace,
    seedWorkspaceContextFiles,
    fetchBriefWhenReady,
    startBriefGeneration,
  ]) {
    m.mockReset().mockResolvedValue(undefined)
  }
  fetchBriefWhenReady.mockResolvedValue(null)
})

describe("finishOnboardingAndEnterApp — brief gated on a data source", () => {
  it("with NO data source: registers the dataset + completes onboarding, but never seeds context or generates a brief", async () => {
    await finishOnboardingAndEnterApp(workspace, "u1", () => {}, false)
    await flush()
    expect(completeOnboarding).toHaveBeenCalledWith("ws1", "u1")
    expect(ensureDatasetForWorkspace).toHaveBeenCalledTimes(1)
    expect(seedWorkspaceContextFiles).not.toHaveBeenCalled()
    expect(fetchBriefWhenReady).not.toHaveBeenCalled()
    expect(startBriefGeneration).not.toHaveBeenCalled()
  })

  it("with a data source: seeds context and kicks generation", async () => {
    await finishOnboardingAndEnterApp(workspace, "u1", () => {}, true)
    await flush()
    expect(seedWorkspaceContextFiles).toHaveBeenCalledTimes(1)
    expect(startBriefGeneration).toHaveBeenCalledWith("acme")
    expect(completeOnboarding).toHaveBeenCalledWith("ws1", "u1")
  })

  it("with a data source and an already-ready brief: adopts it instead of regenerating", async () => {
    fetchBriefWhenReady.mockResolvedValue({ some: "brief" })
    const setContent = vi.fn()
    await finishOnboardingAndEnterApp(workspace, "u1", setContent, true)
    await flush()
    expect(startBriefGeneration).not.toHaveBeenCalled()
    expect(setContent).toHaveBeenCalledWith({ patched: { some: "brief" } })
  })
})
