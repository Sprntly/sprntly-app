// Presentational v4 sign-up scenes (pages 02 credentials + 03 about-you).
// No hooks — the route wires state/handlers in.
import Link from "next/link"
import { AuthShell } from "./AuthShell"
import { PasswordStrengthBar } from "./PasswordStrengthBar"
import { ArrowRight, Eye, EyeOff, Google, Key } from "./icons"

// Roles from design-v4 page 03 ("Who are you?").
export const V4_ROLES = [
  "Founder / CEO",
  "Product Manager",
  "Head of Product / CPO",
  "Engineering",
  "Data / Analytics",
  "Design / UX",
  "Customer Success",
  "Marketing",
  "Operations",
  "Other",
] as const

export type SignUpStep1ViewProps = {
  email: string
  password: string
  showPassword: boolean
  error: string | null
  termsHref: string
  privacyHref: string
  onEmailChange: (v: string) => void
  onPasswordChange: (v: string) => void
  onToggleShowPassword: () => void
  onSubmit: (e: React.FormEvent) => void
  onGoogle: () => void
}

export function SignUpStep1View(props: SignUpStep1ViewProps) {
  return (
    <AuthShell tag="1 of 2 · Create account">
      <div className="auth-h">Create your <em>account.</em></div>
      <div className="auth-sub">Start with the basics. We&apos;ll personalize the rest next.</div>

      <form onSubmit={props.onSubmit}>
        <div className="field">
          <div className="field-l">
            <label htmlFor="email">Email</label> <span className="req">*</span>
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
        <div className="field">
          <div className="field-l">
            <label htmlFor="password">Password</label> <span className="req">*</span>
          </div>
          <div className="inp-pwd-wrap">
            <input
              id="password"
              type={props.showPassword ? "text" : "password"}
              className="inp"
              value={props.password}
              onChange={(e) => props.onPasswordChange(e.target.value)}
              placeholder="Min 8 chars, 1 number, 1 symbol"
              autoComplete="new-password"
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
          <PasswordStrengthBar password={props.password} />
        </div>
        {props.error && <div className="auth-error">{props.error}</div>}
        <button type="submit" className="btn btn-brand btn-block" style={{ marginTop: 6 }}>
          Create account
          <ArrowRight width={14} height={14} />
        </button>
      </form>

      <div className="auth-divider">or continue with</div>
      <div className="sso-row">
        <button type="button" className="sso-btn" onClick={props.onGoogle}>
          <Google />
          Sign up with Google
        </button>
        <button type="button" className="sso-btn" disabled>
          <Key />
          SSO
        </button>
      </div>

      <div className="auth-foot">
        By continuing you agree to our <Link href={props.termsHref}>Terms</Link> and{" "}
        <Link href={props.privacyHref}>Privacy Policy</Link>.
        <br />
        Already have an account? <Link href="/sign-in">Sign in</Link>
      </div>
    </AuthShell>
  )
}

export type SignUpStep2ViewProps = {
  email: string
  firstName: string
  lastName: string
  role: string
  priorities: string
  submitting: boolean
  error: string | null
  onFirstNameChange: (v: string) => void
  onLastNameChange: (v: string) => void
  onRoleChange: (v: string) => void
  onPrioritiesChange: (v: string) => void
  onSubmit: (e: React.FormEvent) => void
  onBack: () => void
}

export function SignUpStep2View(props: SignUpStep2ViewProps) {
  return (
    <AuthShell tag="2 of 2 · About you" cardClassName="auth-card-wide">
      <div className="welcome-banner">
        <span className="wb-icon" aria-hidden>✓</span>
        <div>
          <div className="t">Account created</div>
          <div className="s">{props.email} · ready in seconds</div>
        </div>
      </div>
      <div className="auth-h">Who are <em>you?</em></div>
      <div className="auth-sub">
        A quick name and role so we can tailor the workspace to how you work.
      </div>
      <form onSubmit={props.onSubmit}>
        <div className="auth-form-grid">
          <div className="field">
            <div className="field-l">
              <label htmlFor="firstName">First name</label> <span className="req">*</span>
            </div>
            <input
              id="firstName"
              className="inp"
              value={props.firstName}
              onChange={(e) => props.onFirstNameChange(e.target.value)}
              placeholder="Sarah"
              maxLength={50}
              required
            />
          </div>
          <div className="field">
            <div className="field-l">
              <label htmlFor="lastName">Last name</label> <span className="req">*</span>
            </div>
            <input
              id="lastName"
              className="inp"
              value={props.lastName}
              onChange={(e) => props.onLastNameChange(e.target.value)}
              placeholder="Chen"
              maxLength={50}
              required
            />
          </div>
          <div className="field full">
            <div className="field-l">
              <label htmlFor="role">Your role</label> <span className="req">*</span>
            </div>
            <select
              id="role"
              className="auth-role-select"
              value={props.role}
              onChange={(e) => props.onRoleChange(e.target.value)}
              required
            >
              {V4_ROLES.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
          </div>
          <div className="field full">
            <div className="field-l">
              <label htmlFor="priorities">Your priorities</label>{" "}
              <span className="opt">— what you&apos;re focused on right now</span>
            </div>
            <textarea
              id="priorities"
              className="inp"
              rows={3}
              value={props.priorities}
              onChange={(e) => props.onPrioritiesChange(e.target.value)}
              maxLength={500}
              placeholder="e.g. grow MAU, recover the redesign dip, ship the calorie deficit before Watch 9…"
            />
          </div>
        </div>
        {props.error && <div className="auth-error">{props.error}</div>}
        <button
          type="submit"
          className="btn btn-brand btn-block"
          style={{ marginTop: 10 }}
          disabled={props.submitting}
        >
          {props.submitting ? "Creating account…" : "Continue"}
          {!props.submitting && <ArrowRight width={14} height={14} />}
        </button>
      </form>
      <div className="auth-foot">
        <button type="button" className="auth-link" onClick={props.onBack}>
          Back
        </button>
      </div>
    </AuthShell>
  )
}
