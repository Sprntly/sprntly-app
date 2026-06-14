"use client"

/**
 * SourceTypePills — shared pill selector for design source type.
 *
 * Used by GenerateModal (the popup) and DesignSourceSettings (the settings
 * pane). Renders the `.radio-group` + `.radio-pill` vocabulary that is already
 * CSS-defined in design-agent.css, widened to work outside the modal scope.
 *
 * Props:
 *  value    — currently selected source
 *  onChange — called when the user clicks a different pill
 *  options  — optional override; defaults to the canonical three options in
 *             the same order as the GenerateModal: codebase → Figma → Website
 */

const DEFAULT_OPTIONS: { value: "figma" | "github" | "website"; label: string }[] = [
  { value: "github", label: "From our codebase" },
  { value: "figma", label: "Figma" },
  { value: "website", label: "Website" },
]

interface SourceTypePillsProps {
  value: "figma" | "github" | "website"
  onChange: (v: "figma" | "github" | "website") => void
  options?: { value: "figma" | "github" | "website"; label: string }[]
}

export function SourceTypePills({ value, onChange, options }: SourceTypePillsProps) {
  const opts = options ?? DEFAULT_OPTIONS
  return (
    <div className="radio-group">
      {opts.map((opt) => (
        <button
          key={opt.value}
          type="button"
          className={"radio-pill" + (value === opt.value ? " selected" : "")}
          data-val={opt.value}
          aria-pressed={value === opt.value}
          onClick={() => onChange(opt.value)}
        >
          {opt.label}
        </button>
      ))}
    </div>
  )
}
