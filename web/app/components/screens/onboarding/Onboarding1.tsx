"use client"

import { useNavigation } from "../../../context/NavigationContext"
import { OnboardingLayout } from "./OnboardingLayout"

export function Onboarding1() {
  const { goTo } = useNavigation()

  return (
    <OnboardingLayout
      heroTitle={
        <>
          Know <span>what to build</span> before your standup.
        </>
      }
      heroSub="Sprntly reads signals from your entire stack — analytics, calls, support, reviews, code — and hands you a weekly brief of the three to five things worth shipping."
      proof={
        <div className="ob-proof">
          <div className="ob-proof-item">
            <strong>32</strong>sources
          </div>
          <div className="ob-proof-item">
            <strong>3–5</strong>findings/wk
          </div>
          <div className="ob-proof-item">
            <strong>1 click</strong>to code
          </div>
        </div>
      }
      step={1}
      eyebrow="Get started"
      title="Create your account"
      desc="One account, one product. Invite your team after setup."
    >
      <button className="btn btn-block btn-lg" style={{ marginBottom: 10 }}>
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
          <path
            d="M22.5 12.23c0-.85-.08-1.67-.22-2.45H12v4.64h5.92c-.26 1.37-1.03 2.53-2.19 3.31v2.75h3.54c2.07-1.91 3.27-4.72 3.27-8.25z"
            fill="#4285F4"
          />
          <path
            d="M12 23c2.95 0 5.43-.98 7.24-2.65l-3.54-2.75c-.98.66-2.24 1.05-3.7 1.05-2.84 0-5.25-1.92-6.11-4.5H2.22v2.83A10.99 10.99 0 0 0 12 23z"
            fill="#34A853"
          />
          <path
            d="M5.89 14.15a6.6 6.6 0 0 1 0-4.3V7.02H2.22a11 11 0 0 0 0 9.96l3.67-2.83z"
            fill="#FBBC05"
          />
          <path
            d="M12 5.5c1.6 0 3.05.55 4.18 1.64l3.14-3.14C17.42 2.18 14.95 1 12 1 7.7 1 3.99 3.47 2.22 7.02l3.67 2.83C6.75 7.27 9.16 5.5 12 5.5z"
            fill="#EA4335"
          />
        </svg>
        Continue with Google
      </button>
      <div className="divider">or</div>
      <div className="field">
        <label className="field-label">Work email</label>
        <input type="email" className="input" placeholder="you@company.com" />
      </div>
      <button
        className="btn btn-primary btn-block btn-lg"
        onClick={() => goTo("ob-2")}
      >
        Continue
      </button>
      <p
        style={{
          textAlign: "center",
          fontSize: 11.5,
          color: "var(--muted)",
          marginTop: 16,
        }}
      >
        By continuing you agree to our{" "}
        <a href="#" style={{ color: "var(--ink-3)" }}>
          terms
        </a>{" "}
        and{" "}
        <a href="#" style={{ color: "var(--ink-3)" }}>
          privacy policy
        </a>
        .
      </p>
    </OnboardingLayout>
  )
}
