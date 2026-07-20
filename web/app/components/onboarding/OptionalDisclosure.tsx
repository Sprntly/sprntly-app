"use client"

import { useState, type ReactNode } from "react"
import { Plus } from "../auth/icons"

/**
 * Progressive disclosure for OPTIONAL onboarding fields (registration spec
 * 2026-07: minimal fields per screen). Renders a collapsed "+ Add …" row;
 * clicking expands the children inline. Collapsing again keeps whatever was
 * typed — the step's own state owns the values, this only controls visibility.
 */
export function OptionalDisclosure({
  label,
  hint = "optional — you can finish this later in Settings",
  children,
  defaultOpen = false,
}: {
  /** e.g. "Add mission & strategy" */
  label: string
  hint?: string
  children: ReactNode
  defaultOpen?: boolean
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="onb-disclosure" data-open={open ? "true" : "false"}>
      <button
        type="button"
        className="onb-disclosure-toggle"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <Plus
          style={{
            width: 12,
            height: 12,
            transform: open ? "rotate(45deg)" : undefined,
            transition: "transform 0.15s",
          }}
          aria-hidden
        />
        <span className="t">{label}</span>
        <span className="s">{hint}</span>
      </button>
      {open && <div className="onb-disclosure-body">{children}</div>}
    </div>
  )
}
