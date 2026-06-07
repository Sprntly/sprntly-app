"use client"

import { passwordStrength, type PasswordStrength } from "../../lib/auth-validation"

// v4 design: four segmented bars + a single hint line. The number of lit
// segments and their color tier come from the existing passwordStrength()
// logic — only the presentation is restyled to match design page 02.
const SEGMENTS: Record<PasswordStrength, number> = {
  weak: 1,
  fair: 2,
  good: 3,
  strong: 4,
}

// .on1/.on2/.on3 map to the design's red/amber/green bar colors.
const TIER: Record<PasswordStrength, string> = {
  weak: "on1",
  fair: "on2",
  good: "on3",
  strong: "on3",
}

const HINT: Record<PasswordStrength, string> = {
  weak: "Weak — add length, a number and a symbol",
  fair: "Fair — add a number or symbol",
  good: "Good — almost there",
  strong: "Strong — meets all requirements ✓",
}

export function PasswordStrengthBar({ password }: { password: string }) {
  const strength = passwordStrength(password)
  if (!password) return null

  const lit = SEGMENTS[strength]
  const tier = TIER[strength]

  return (
    <>
      <div className="pwd-strength" data-strength={strength}>
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className={`pwd-bar${i < lit ? ` ${tier}` : ""}`} />
        ))}
      </div>
      <div className="pwd-hint">{HINT[strength]}</div>
    </>
  )
}
