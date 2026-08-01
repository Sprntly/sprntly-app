// Presentational reset-password scene. No hooks — the route wires state
// and handlers in. Lets the structure be asserted via renderToStaticMarkup.
import Link from "next/link"
import { AuthShell } from "./AuthShell"
import { OtpInput } from "./OtpInput"
import { PasswordStrengthBar } from "./PasswordStrengthBar"
import { Eye, EyeOff, InfoCircle, Refresh } from "./icons"

/** "code" gates the form: the recovery email carries a 6-digit code, and
 *  redeeming it is what mints the session updateUser needs. */
export type ResetPasswordMode = "code" | "form" | "done" | "no-session"

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
  /* --- code mode --- */
  email?: string
  code?: string
  message?: string | null
  resendCooldown?: number
  canResend?: boolean
  onCodeChange?: (v: string) => void
  onCodeSubmit?: (e: React.FormEvent) => void
  onResend?: () => void
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
        <div className="auth-sub">Your new password is updated. You&apos;re signed in.</div>
        <div style={{ marginTop: 16 }}>
          <Link href="/" className="btn btn-brand btn-block">
            Continue to Sprntly
          </Link>
        </div>
      </AuthShell>
    )
  }

  if (mode === "code") {
    const code = props.code ?? ""
    return (
      <AuthShell tag="Password reset" cardClassName="auth-card-center">
        <div className="auth-h">Check your <em>email.</em></div>
        <div className="auth-sub">
          If an account exists, we sent a 6-digit reset code. Enter it below to
          choose a new password.
        </div>
        {props.email && <div className="verify-email">{props.email}</div>}

        <form onSubmit={props.onCodeSubmit}>
          <OtpInput
            value={code}
            onChange={props.onCodeChange ?? (() => {})}
            disabled={submitting}
            autoFocus
            invalid={!!error}
            ariaLabel="Password reset code"
            idPrefix="reset-code"
          />

          {error && (
            <p className="auth-error" role="alert">
              {error}
            </p>
          )}
          {props.message && <div className="auth-msg">{props.message}</div>}

          <button
            type="submit"
            className="btn btn-brand btn-block"
            disabled={submitting || code.length < 6}
          >
            {submitting ? "Verifying…" : "Verify code"}
          </button>
        </form>

        <button
          type="button"
          className="btn btn-ghost btn-block"
          style={{ marginTop: 8, fontSize: 12, padding: 9 }}
          onClick={props.onResend}
          disabled={!props.canResend}
        >
          <Refresh width={13} height={13} />
          {(props.resendCooldown ?? 0) > 0 ? (
            <>
              Resend code <span className="muted">({props.resendCooldown}s)</span>
            </>
          ) : (
            "Resend code"
          )}
        </button>
        <div className="spam-note">
          <InfoCircle width={14} height={14} />
          <div>Check your spam folder if it doesn&apos;t arrive. Code expires in 1 hour.</div>
        </div>
        <div className="auth-foot">
          <Link href="/sign-in" className="auth-link">Back to sign in</Link>
        </div>
      </AuthShell>
    )
  }

  if (mode === "no-session") {
    return (
      <AuthShell tag="Password reset">
        <div className="auth-h">Reset <em>expired.</em></div>
        <div className="auth-sub">
          This reset request is invalid or has expired. Request a new one and sign in again.
        </div>
        <div style={{ marginTop: 16 }}>
          <Link href="/sign-in" className="btn btn-brand btn-block">
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
          <div className="inp-pwd-wrap">
            <input
              id="new-password"
              className="inp"
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
              className="pwd-toggle"
              aria-label={showPassword ? "Hide password" : "Show password"}
              onClick={onToggleShowPassword}
            >
              {showPassword ? <EyeOff /> : <Eye />}
            </button>
          </div>
          <PasswordStrengthBar password={newPassword} />
        </div>

        <div className="field">
          <div className="field-l">
            <label htmlFor="confirm-password">Confirm password</label> <span className="req">*</span>
          </div>
          <input
            id="confirm-password"
            className="inp"
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
          className="btn btn-brand btn-block"
          disabled={submitting}
        >
          {submitting ? "Updating…" : "Update password"}
        </button>
      </form>

      <div className="auth-foot">
        <Link href="/sign-in" className="auth-link">Back to sign in</Link>
      </div>
    </AuthShell>
  )
}
