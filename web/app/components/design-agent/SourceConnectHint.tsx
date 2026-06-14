"use client"

import { redirectToConnect } from "./DesignAgentDrawer"

interface SourceConnectHintProps {
  provider: "figma" | "github"
}

export function SourceConnectHint({ provider }: SourceConnectHintProps) {
  if (provider === "figma") {
    return (
      <>
        <span className="src-not-connected">⚠ Not connected</span>
        <button
          type="button"
          className="src-connect-btn"
          onClick={() => void redirectToConnect("figma")}
        >
          Connect Figma →
        </button>
      </>
    )
  }

  return (
    <>
      <span className="src-not-connected muted">Not connected</span>
      <button
        type="button"
        className="src-connect-btn ghost"
        onClick={() => void redirectToConnect("github")}
      >
        Connect a repo →
      </button>
    </>
  )
}
