"use client"

import { passwordStrength, type PasswordStrength } from "../../lib/auth-validation"

export function PasswordStrengthBar({ password }: { password: string }) {
  const strength = passwordStrength(password)
  if (!password) return null

  const labels: Record<PasswordStrength, string> = {
    weak: "Weak",
    fair: "Fair",
    good: "Good",
    strong: "Strong",
  }
  const widths: Record<PasswordStrength, string> = {
    weak: "25%",
    fair: "50%",
    good: "75%",
    strong: "100%",
  }

  return (
    <div className="pw-strength">
      <div className="pw-bar">
        <div className={`pw-fill pw-${strength}`} style={{ width: widths[strength] }} />
      </div>
      <span className={`pw-label pw-${strength}`}>{labels[strength]}</span>
      <style jsx>{`
        .pw-strength {
          display: flex;
          align-items: center;
          gap: 10px;
          margin-top: 6px;
        }
        .pw-bar {
          flex: 1;
          height: 4px;
          background: var(--line);
          border-radius: 999px;
          overflow: hidden;
        }
        .pw-fill {
          height: 100%;
          border-radius: 999px;
          transition: width 0.2s;
        }
        .pw-weak {
          background: #c0392b;
        }
        .pw-fair {
          background: #d68910;
        }
        .pw-good {
          background: #27ae60;
        }
        .pw-strong {
          background: var(--accent);
        }
        .pw-label {
          font-size: 11px;
          font-weight: 500;
          min-width: 44px;
        }
      `}</style>
    </div>
  )
}
