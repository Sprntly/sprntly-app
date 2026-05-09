"use client"

import type { ReactNode } from "react"

interface OnboardingLayoutProps {
  heroTitle: ReactNode
  heroSub: string
  proof?: ReactNode
  step: number
  eyebrow: string
  title: string
  desc: string
  children: ReactNode
}

export function OnboardingLayout({
  heroTitle,
  heroSub,
  proof,
  step,
  eyebrow,
  title,
  desc,
  children,
}: OnboardingLayoutProps) {
  return (
    <div className="ob-shell">
      <div className="ob-hero">
        <div className="ob-hero-inner">
          <div className="ob-logo">
            spr<span>ntly</span>
          </div>
          <h1 className="ob-headline">{heroTitle}</h1>
          <p className="ob-sub">{heroSub}</p>
        </div>
        {proof}
      </div>
      <div className="ob-panel">
        <div className="ob-panel-inner">
          <div className="ob-brand-mark">
            spr<span>ntly</span>
          </div>
          <div className="ob-step-indicator">
            {[1, 2, 3, 4, 5, 6, 7, 8].map((s) => (
              <div
                key={s}
                className={`ob-dot ${s < step ? "done" : ""} ${
                  s === step ? "active" : ""
                }`}
              />
            ))}
          </div>
          <div className="ob-eyebrow">{eyebrow}</div>
          <h2 className="ob-title">{title}</h2>
          <p className="ob-desc">{desc}</p>
          {children}
        </div>
      </div>
    </div>
  )
}
