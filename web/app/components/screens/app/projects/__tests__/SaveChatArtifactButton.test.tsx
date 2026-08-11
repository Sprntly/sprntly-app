// @vitest-environment jsdom
//
// SaveChatArtifactButton — the item-14 substrate's standalone UI affordance.
// Covers: calls `projectsApi.saveChatArtifact` with the given content/title,
// disables while saving, surfaces success via `onSaved`, and surfaces a
// failure inline without crashing.
import * as React from "react"
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const saveChatArtifactMock = vi.fn()

vi.mock("../../../../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../../../../lib/api")>("../../../../../lib/api")
  return {
    ...actual,
    projectsApi: {
      ...actual.projectsApi,
      saveChatArtifact: (...a: unknown[]) => saveChatArtifactMock(...a),
    },
  }
})

import { ApiError } from "../../../../../lib/api"
import { SaveChatArtifactButton } from "../SaveChatArtifactButton"

afterEach(() => {
  cleanup()
  saveChatArtifactMock.mockReset()
})

describe("SaveChatArtifactButton", () => {
  it("calls saveChatArtifact with the project id and content on click", async () => {
    saveChatArtifactMock.mockResolvedValue({ artifact_type: "report", artifact_id: 7, project_id: 3 })
    const onSaved = vi.fn()
    const content = "## Prioritization\n- A\n- B"

    render(<SaveChatArtifactButton projectId={3} content={content} onSaved={onSaved} />)
    fireEvent.click(screen.getByRole("button", { name: /save as artifact/i }))

    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(7))
    expect(saveChatArtifactMock).toHaveBeenCalledWith(3, { content, title: undefined })
  })

  it("passes an explicit title through untouched", async () => {
    saveChatArtifactMock.mockResolvedValue({ artifact_type: "report", artifact_id: 1, project_id: 3 })

    render(<SaveChatArtifactButton projectId={3} content="body" title="My brief" />)
    fireEvent.click(screen.getByRole("button", { name: /save as artifact/i }))

    await waitFor(() =>
      expect(saveChatArtifactMock).toHaveBeenCalledWith(3, { content: "body", title: "My brief" }),
    )
  })

  it("disables the button while the save is in flight", async () => {
    let resolveSave: (v: { artifact_type: "report"; artifact_id: number; project_id: number }) => void =
      () => {}
    saveChatArtifactMock.mockReturnValue(
      new Promise((resolve) => {
        resolveSave = resolve
      }),
    )

    render(<SaveChatArtifactButton projectId={3} content="body" />)
    const button = screen.getByRole("button", { name: /save as artifact/i }) as HTMLButtonElement
    fireEvent.click(button)

    expect((screen.getByRole("button", { name: /saving/i }) as HTMLButtonElement).disabled).toBe(true)

    resolveSave({ artifact_type: "report", artifact_id: 1, project_id: 3 })
    await waitFor(() =>
      expect((screen.getByRole("button", { name: /save as artifact/i }) as HTMLButtonElement).disabled).toBe(
        false,
      ),
    )
  })

  it("does not call the API for whitespace-only content", () => {
    render(<SaveChatArtifactButton projectId={3} content="   " />)
    fireEvent.click(screen.getByRole("button", { name: /save as artifact/i }))
    expect(saveChatArtifactMock).not.toHaveBeenCalled()
  })

  it("surfaces a failure inline instead of crashing", async () => {
    saveChatArtifactMock.mockRejectedValue(new ApiError(502, { detail: "Could not save chat output as an artifact" }))

    render(<SaveChatArtifactButton projectId={3} content="body" />)
    fireEvent.click(screen.getByRole("button", { name: /save as artifact/i }))

    const alert = await screen.findByRole("alert")
    expect(alert.textContent).toMatch(/couldn't save/i)
  })
})
