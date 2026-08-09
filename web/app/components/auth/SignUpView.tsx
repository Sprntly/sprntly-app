// Presentational v4 sign-up scenes (pages 02 credentials + 03 about-you).
// No hooks — the route wires state/handlers in.
import Link from "next/link"
import { AuthShell } from "./AuthShell"
import { PasswordStrengthBar } from "./PasswordStrengthBar"
import { ArrowRight, Eye, EyeOff, Google, Key, Spinner } from "./icons"
import { ShareContextStrip } from "../shared/ShareContextStrip"

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
  confirmPassword: string
  showPassword: boolean
  /** True while the email-availability check is in flight. */
  submitting: boolean
  /** True once the Google OAuth redirect has been kicked off — the browser
   *  leaves this page, so the button stays busy rather than resetting. */
  googleSubmitting: boolean
  error: string | null
  termsHref: string
  privacyHref: string
  onEmailChange: (v: string) => void
  onPasswordChange: (v: string) => void
  onConfirmPasswordChange: (v: string) => void
  onToggleShowPassword: () => void
  onSubmit: (e: React.FormEvent) => void
  onGoogle: () => void
  /** Set only when this sign-up originated from a valid `?share=` artifact
   *  link — mounts the ShareContextStrip + a domain-naming hint. Absent (the
   *  default) renders byte-identically to before this ticket. */
  shareContext?: { title: string; sharerName: string; requiredDomain: string | null }
}

export function SignUpStep1View(props: SignUpStep1ViewProps) {
  return (
    <AuthShell tag="1 of 2 · Create account">
      {props.shareContext && (
        <ShareContextStrip
          kind="sign-up"
          title={props.shareContext.title}
          sharerName={props.shareContext.sharerName}
        />
      )}
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
          {props.shareContext?.requiredDomain && (
            <div className="field-hint" data-testid="sign-up-domain-hint">
              Use your {props.shareContext.requiredDomain} work email to view what was shared with you.
            </div>
          )}
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
              placeholder="Min 8 chars, 1 uppercase, 1 number"
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
        <div className="field">
          <div className="field-l">
            <label htmlFor="confirm-password">Confirm password</label>{" "}
            <span className="req">*</span>
          </div>
          <div className="inp-pwd-wrap">
            <input
              id="confirm-password"
              type={props.showPassword ? "text" : "password"}
              className="inp"
              value={props.confirmPassword}
              onChange={(e) => props.onConfirmPasswordChange(e.target.value)}
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
          {/* Live match feedback, so a typo is caught here rather than on submit.
              Stays silent until they've actually started the confirm field. */}
          {props.confirmPassword.length > 0 &&
            (props.password === props.confirmPassword ? (
              <div className="pwd-hint" style={{ color: "var(--accent)" }}>
                Passwords match
              </div>
            ) : (
              <div className="pwd-hint">Passwords don&apos;t match yet</div>
            ))}
        </div>
        {props.error && <div className="auth-error">{props.error}</div>}
        {/* Busy for the whole email-availability round-trip, so the click has
            visible feedback instead of a silent pause before step 2. */}
        <button
          type="submit"
          className="btn btn-brand btn-block"
          style={{ marginTop: 6 }}
          disabled={props.submitting || props.googleSubmitting}
          aria-busy={props.submitting}
        >
          {props.submitting ? "Checking…" : "Create account"}
          {props.submitting ? (
            <Spinner width={14} height={14} />
          ) : (
            <ArrowRight width={14} height={14} />
          )}
        </button>
      </form>

      <div className="auth-divider">or continue with</div>
      <div className="sso-row">
        <button
          type="button"
          className="sso-btn"
          onClick={props.onGoogle}
          disabled={props.submitting || props.googleSubmitting}
          aria-busy={props.googleSubmitting}
        >
          {props.googleSubmitting ? <Spinner /> : <Google />}
          {props.googleSubmitting ? "Redirecting…" : "Sign up with Google"}
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
  submitting: boolean
  error: string | null
  onFirstNameChange: (v: string) => void
  onLastNameChange: (v: string) => void
  onRoleChange: (v: string) => void
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
        </div>
        {props.error && <div className="auth-error">{props.error}</div>}
        {/* `submitting` stays true through the redirect that follows a
            successful signup (see sign-up/page.tsx) — the account call and the
            route change together are seconds of wait, and the button must not
            flip back to "Continue" in the middle of it. */}
        <button
          type="submit"
          className="btn btn-brand btn-block"
          style={{ marginTop: 10 }}
          disabled={props.submitting}
          aria-busy={props.submitting}
        >
          {props.submitting ? "Creating account…" : "Continue"}
          {props.submitting ? (
            <Spinner width={14} height={14} />
          ) : (
            <ArrowRight width={14} height={14} />
          )}
        </button>
      </form>
      <div className="auth-foot">
        <button
          type="button"
          className="auth-link"
          onClick={props.onBack}
          disabled={props.submitting}
        >
          Back
        </button>
      </div>
    </AuthShell>
  )
}
