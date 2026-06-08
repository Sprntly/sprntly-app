// Presentational reset-password scene. No hooks — the route wires state
// and handlers in. Lets the structure be asserted via renderToStaticMarkup.
import Link from "next/link"
import { AuthShell } from "./AuthShell"
import { Eye, EyeOff } from "./icons"

export type ResetPasswordMode = "form" | "done" | "no-session"

export type ResetPasswordViewProps = {
  mode: ResetPasswordMode
  newPassword: string
  confirmPassword: string
  showPassword: boolean
  submitting: boolean
  error: string | null
  onNewPasswordChange: (v: string) => void
  onConfirmPasswordChange: (v: string) => void
  onToggleShowPassword: () => void
  onSubmit: (e: React.FormEvent) => void
}

export function ResetPasswordView(props: ResetPasswordViewProps) {
  const {
    mode,
    newPassword,
    confirmPassword,
    showPassword,
    submitting,
    error,
    onNewPasswordChange,
    onConfirmPasswordChange,
    onToggleShowPassword,
    onSubmit,
  } = props

  if (mode === "done") {
    return (
      <AuthShell tag="Password reset">
        <div className="auth-h">All <em>set.</em></div>
        <div className="auth-sub">Your new password is updated. You're signed in.</div>
        <div style={{ marginTop: 16 }}>
          <Link href="/" className="btn btn-primary btn-block">
            Continue to Sprntly
          </Link>
        </div>
      </AuthShell>
    )
  }

  if (mode === "no-session") {
    return (
      <AuthShell tag="Password reset">
        <div className="auth-h">Link <em>expired.</em></div>
        <div className="auth-sub">
          This reset link is invalid or has expired. Request a new one and sign in again.
        </div>
        <div style={{ marginTop: 16 }}>
          <Link href="/sign-in" className="btn btn-primary btn-block">
            Back to sign in
          </Link>
        </div>
      </AuthShell>
    )
  }

  return (
    <AuthShell tag="Password reset">
      <div className="auth-h">Set a new <em>password.</em></div>
      <div className="auth-sub">Choose a strong password to finish resetting your account.</div>

      <form onSubmit={onSubmit}>
        <div className="field">
          <div className="field-l">
            <label htmlFor="new-password">New password</label> <span className="req">*</span>
          </div>
          <div className="field-with-icon">
            <input
              id="new-password"
              type={showPassword ? "text" : "password"}
              value={newPassword}
              onChange={(e) => onNewPasswordChange(e.target.value)}
              autoComplete="new-password"
              required
              minLength={8}
              placeholder="At least 8 characters"
            />
            <button
              type="button"
              className="field-icon-btn"
              aria-label={showPassword ? "Hide password" : "Show password"}
              onClick={onToggleShowPassword}
            >
              {showPassword ? <EyeOff /> : <Eye />}
            </button>
          </div>
        </div>

        <div className="field">
          <div className="field-l">
            <label htmlFor="confirm-password">Confirm password</label> <span className="req">*</span>
          </div>
          <input
            id="confirm-password"
            type={showPassword ? "text" : "password"}
            value={confirmPassword}
            onChange={(e) => onConfirmPasswordChange(e.target.value)}
            autoComplete="new-password"
            required
            minLength={8}
            placeholder="Repeat the new password"
          />
        </div>

        {error && (
          <p className="auth-error" role="alert">
            {error}
          </p>
        )}

        <button
          type="submit"
          className="btn btn-primary btn-block"
          disabled={submitting}
        >
          {submitting ? "Updating…" : "Update password"}
        </button>
      </form>
    </AuthShell>
  )
}
