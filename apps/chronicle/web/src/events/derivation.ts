/**
 * Derivation line for automatic events (AI-003 / Table 15).
 * e.g. "Generated 2026-07-13 · event-v1 · model route X · scope qs_…"
 */

export function formatDerivationLine(
  derivation: Record<string, unknown> | null | undefined,
): string | null {
  if (!derivation || typeof derivation !== 'object') return null
  const parts: string[] = []

  const generatedAt = derivation.generated_at
  if (typeof generatedAt === 'string' && generatedAt.length >= 10) {
    parts.push(`Generated ${generatedAt.slice(0, 10)}`)
  } else {
    parts.push('Generated')
  }

  const process =
    (typeof derivation.process_version === 'string' && derivation.process_version) ||
    (typeof derivation.policy_version === 'string' && derivation.policy_version) ||
    null
  if (process) parts.push(process)

  const route =
    typeof derivation.model_route === 'string' ? derivation.model_route : null
  if (route) parts.push(`model route ${route}`)

  const scope =
    typeof derivation.scope_fingerprint === 'string'
      ? derivation.scope_fingerprint
      : null
  if (scope) {
    const short = scope.length > 16 ? `${scope.slice(0, 16)}…` : scope
    parts.push(`scope ${short}`)
  }

  return parts.join(' · ')
}
