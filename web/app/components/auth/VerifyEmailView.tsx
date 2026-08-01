// Presentational v4 verify-email scene (page 04). No hooks.
//
// Signup confirmation is a typed 6-digit code, not an emailed link — see
// supabase/templates/confirmation.html. The route redeems it via
// auth.verifyEmailOtp, which mints the session here rather than on
// /auth/confirm.
import Link from "next/link"
import { AuthShell } from "./AuthShell"
import { OtpInput } from "./OtpInput"
import { InfoCircle, MailCheck, Refresh } from "./icons"
import { ShareContextStrip } from "../shared/ShareContextStrip"

export type VerifyEmailViewProps = {
  email: string
  code: string
  message: string | null
  error: string | null
  submitting: boolean
  resendCooldown: number
  canResend: boolean
  onCodeChange: (next: string) => void
  onSubmit: (e: React.FormEvent) => void
  onResend: () => void
  /** Set only when this verification originated from a valid `?share=`
   *  artifact link. Absent renders unchanged from the base signup flow. */
  shareContext?: { title: string; sharerName: string }
}

export function VerifyEmailView(props: VerifyEmailViewProps) {
  return (
    <AuthShell tag="Verify email" cardClassName="auth-card-center">
      {props.shareContext && (
        <ShareContextStrip
          kind="verify"
          title={props.shareContext.title}
          sharerName={props.shareContext.sharerName}
        />
      )}
      <div className="verify-icon">
        <MailCheck width={30} height={30} />
      </div>
      <div className="auth-h">Check your <em>inbox.</em></div>
      <div className="auth-sub">
        We sent a 6-digit verification code to your work email. Enter it below to
        continue.
      </div>
      <div className="verify-email">{props.email || "your work email"}</div>

      <form onSubmit={props.onSubmit}>
        <OtpInput
          value={props.code}
          onChange={props.onCodeChange}
          disabled={props.submitting}
          autoFocus
          invalid={!!props.error}
          ariaLabel="Email verification code"
          idPrefix="verify-code"
        />

        {props.error && (
          <p className="auth-error" role="alert">
            {props.error}
          </p>
        )}
        {props.message && <div className="auth-msg">{props.message}</div>}

        <button
          type="submit"
          className="btn btn-brand btn-block"
          disabled={props.submitting || props.code.length < 6}
        >
          {props.submitting ? "Verifying…" : "Verify email"}
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
        {props.resendCooldown > 0 ? (
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
        Wrong address? <Link href="/sign-up">Create a new account</Link> ·{" "}
        <Link href="/sign-in">Sign in</Link>
      </div>
    </AuthShell>
  )
}
