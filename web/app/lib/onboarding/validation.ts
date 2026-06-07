/**
 * Lightweight required-field validation for the v4 onboarding steps.
 *
 * Steps own their state; this module just turns a flat list of field
 * checks into (a) a key→message error map, (b) the first invalid key (for
 * focus), and (c) a boolean. Kept framework-free and pure so it can be
 * unit-tested without a DOM and reused across InterviewLayout-based steps.
 */

export type FieldCheck = {
  /** Stable key used to tag the field for error display + focus. */
  key: string
  /** True when the field is satisfied. */
  valid: boolean
  /** Message shown under the field when invalid. */
  message: string
}

export type ValidationResult = {
  ok: boolean
  /** key → message, only for invalid fields. */
  errors: Record<string, string>
  /** First invalid field key in declared order, or null when all valid. */
  firstInvalid: string | null
}

export function validateRequired(checks: FieldCheck[]): ValidationResult {
  const errors: Record<string, string> = {}
  let firstInvalid: string | null = null
  for (const c of checks) {
    if (!c.valid) {
      errors[c.key] = c.message
      if (firstInvalid === null) firstInvalid = c.key
    }
  }
  return { ok: firstInvalid === null, errors, firstInvalid }
}

/** Convenience: a check that passes when the trimmed value is non-empty. */
export function requireText(
  key: string,
  value: string,
  message: string,
): FieldCheck {
  return { key, valid: value.trim().length > 0, message }
}
