// Presentational v4 sign-in scene (page 01). No hooks — the route wires
// state and handlers in. Lets the structure be asserted via renderToStaticMarkup.
import Link from "next/link"
import { AuthShell } from "./AuthShell"
import { ArrowRight, Eye, EyeOff, Google, Key } from "./icons"

export type SignInViewProps = {
  email: string
  password: string
  showPassword: boolean
  submitting: boolean
  error: string | null
  forgotMode: boolean
  lockoutMs: number
  termsHref: string
  privacyHref: string
  onEmailChange: (v: string) => void
  onPasswordChange: (v: string) => void
  onToggleShowPassword: () => void
  onSubmit: (e: React.FormEvent) => void
  onGoogle: () => void
  onEnterForgot: () => void
  onExitForgot: () => void
}

export function SignInView(props: SignInViewProps) {
  const { forgotMode } = props
  return (
    <AuthShell tag="The OS that turns your product into a self-improving recursive AI loop">
      <div className="auth-h">Welcome <em>back.</em></div>
      <div className="auth-sub">Sign in to your workspace to pick up where you left off.</div>

      <form onSubmit={props.onSubmit}>
        <div className="field">
          <div className="field-l">
            <label htmlFor="email">Work email</label> <span className="req">*</span>
          </div>
          <input
            id="email"
            type="email"
            className="inp"
            value={props.email}
            onChange={(e) => props.onEmailChange(e.target.value)}
            autoComplete="email"
            required
          />
        </div>
        {!forgotMode && (
          <div className="field">
            <div className="field-l">
              <label htmlFor="password">Password</label> <span className="req">*</span>
              <span className="right">
                <button type="button" className="auth-link" onClick={props.onEnterForgot}>
                  Forgot?
                </button>
              </span>
            </div>
            <div className="inp-pwd-wrap">
              <input
                id="password"
                type={props.showPassword ? "text" : "password"}
                className="inp"
                value={props.password}
                onChange={(e) => props.onPasswordChange(e.target.value)}
                autoComplete="current-password"
                required
              />
              <button
                type="button"
                className="pwd-toggle"
                aria-label={props.showPassword ? "Hide password" : "Show password"}
                onClick={props.onToggleShowPassword}
              >
                {props.showPassword ? <EyeOff /> : <Eye />}
              </button>
            </div>
          </div>
        )}
        {props.lockoutMs > 0 && (
          <div className="auth-error">
            Too many attempts. Try again in {Math.ceil(props.lockoutMs / 60000)} min.
          </div>
        )}
        {props.error && <div className="auth-error">{props.error}</div>}
        <button
          type="submit"
          className="btn btn-brand btn-block"
          style={{ marginTop: 8 }}
          disabled={props.submitting || props.lockoutMs > 0}
        >
          {props.submitting ? "…" : forgotMode ? "Send reset link" : "Sign in"}
          {!props.submitting && !forgotMode && <ArrowRight width={14} height={14} />}
        </button>
      </form>

      {!forgotMode && (
        <>
          <div className="auth-divider">or continue with</div>
          <div className="sso-row">
            <button type="button" className="sso-btn" onClick={props.onGoogle}>
              <Google />
              Google
            </button>
            <button type="button" className="sso-btn" disabled>
              <Key />
              SSO
            </button>
          </div>
        </>
      )}

      {!forgotMode ? (
        <div className="auth-foot">
          New to Sprntly? <Link href="/sign-up">Create an account</Link>
        </div>
      ) : (
        <div className="auth-foot">
          <button type="button" className="auth-link" onClick={props.onExitForgot}>
            Back to sign in
          </button>
        </div>
      )}

      <div className="auth-foot" style={{ marginTop: 12 }}>
        <Link href={props.termsHref}>Terms</Link> · <Link href={props.privacyHref}>Privacy</Link>
      </div>
    </AuthShell>
  )
}
