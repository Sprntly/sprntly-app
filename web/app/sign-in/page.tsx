"use client"

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { useAuth } from "../lib/auth"
import { ApiError } from "../lib/api"
import { publicPath } from "../lib/public-path"

export default function SignInPage() {
  const auth = useAuth()
  const router = useRouter()
  const [password, setPassword] = useState("")
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (auth.kind === "authed") {
      router.replace("/")
    }
  }, [auth.kind, router])

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      await auth.signIn(password)
      router.replace("/")
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        setError("Wrong password")
      } else {
        setError("Couldn't sign in. Try again in a moment.")
      }
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="signin-shell">
      <form className="signin-card" onSubmit={onSubmit}>
        <div className="signin-brand">Sprntly</div>
        <div className="signin-eyebrow">Demo access</div>
        <p className="signin-blurb">
          Enter the demo password to view a live weekly brief generated from real product data.
        </p>
        <label className="signin-label">
          Password
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoFocus
            autoComplete="current-password"
            required
          />
        </label>
        {error && <div className="signin-error">{error}</div>}
        <button type="submit" disabled={submitting || !password}>
          {submitting ? "Signing in..." : "Sign in"}
        </button>
        <div className="signin-footer">
          Don&apos;t have a password? Email{" "}
          <a href="mailto:apurvajain.kota@gmail.com">apurvajain.kota@gmail.com</a>
          <br />
          <a href={publicPath("/privacy")}>Privacy Policy</a>
          {" · "}
          <a href={publicPath("/terms")}>Terms of Use</a>
        </div>
      </form>
      <style jsx>{`
        .signin-shell {
          min-height: 100vh;
          display: flex;
          align-items: center;
          justify-content: center;
          background: #0a0a0c;
          color: #e6e6ea;
          font-family: "Geist", "Inter", system-ui, sans-serif;
          padding: 24px;
        }
        .signin-card {
          width: 100%;
          max-width: 380px;
          background: #131318;
          border: 1px solid #232329;
          border-radius: 16px;
          padding: 28px;
          display: flex;
          flex-direction: column;
          gap: 14px;
          box-shadow: 0 30px 80px rgba(0, 0, 0, 0.4);
        }
        .signin-brand {
          font-family: "General Sans", "Geist", sans-serif;
          font-weight: 700;
          font-size: 22px;
          letter-spacing: -0.02em;
        }
        .signin-eyebrow {
          font-size: 11px;
          text-transform: uppercase;
          letter-spacing: 0.18em;
          color: #7a7a85;
        }
        .signin-blurb {
          font-size: 14px;
          color: #a8a8b3;
          line-height: 1.5;
          margin: 0 0 4px 0;
        }
        .signin-label {
          display: flex;
          flex-direction: column;
          gap: 6px;
          font-size: 12px;
          color: #a8a8b3;
        }
        input[type="password"] {
          background: #0a0a0c;
          border: 1px solid #2a2a32;
          color: #e6e6ea;
          font-size: 15px;
          font-family: "JetBrains Mono", monospace;
          padding: 12px 14px;
          border-radius: 10px;
          outline: none;
          transition: border-color 0.15s;
        }
        input[type="password"]:focus {
          border-color: #4a4a55;
        }
        button {
          background: #e6e6ea;
          color: #0a0a0c;
          font-weight: 600;
          font-size: 14px;
          padding: 12px;
          border-radius: 10px;
          border: none;
          cursor: pointer;
          transition: opacity 0.15s;
        }
        button:disabled {
          opacity: 0.5;
          cursor: not-allowed;
        }
        .signin-error {
          color: #ff6b6b;
          font-size: 13px;
          padding: 8px 12px;
          background: rgba(255, 107, 107, 0.08);
          border-radius: 8px;
        }
        .signin-footer {
          font-size: 12px;
          color: #7a7a85;
          margin-top: 4px;
          text-align: center;
        }
        .signin-footer a {
          color: #a8a8b3;
          text-decoration: underline;
        }
      `}</style>
    </div>
  )
}
