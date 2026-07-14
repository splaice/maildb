import { apiFetch, apiGet } from '../api/client'
import type { SearchMode } from '../api/types'

export interface AiSettings {
  ask_enabled: boolean
  interpret_enabled: boolean
  generate_enabled: boolean
  answer_model: string
  retention_note: string
}

export interface PrivacySettings {
  session_max_age_s: number
}

export interface SearchSettings {
  default_mode: SearchMode
}

export interface ChronicleSettingsDoc {
  default_lanes: string[]
}

export interface AppSettingsDocument {
  ai: AiSettings
  privacy: PrivacySettings
  search: SearchSettings
  chronicle: ChronicleSettingsDoc
}

/** Partial shallow patch (same shape as PUT body). */
export type AppSettingsPatch = {
  ai?: Partial<Pick<AiSettings, 'ask_enabled' | 'interpret_enabled' | 'generate_enabled' | 'retention_note'>>
  privacy?: Partial<PrivacySettings>
  search?: Partial<SearchSettings>
  chronicle?: Partial<ChronicleSettingsDoc>
}

export function fetchSettings(signal?: AbortSignal): Promise<AppSettingsDocument> {
  return apiGet<AppSettingsDocument>('/api/settings', signal)
}

export function putSettings(
  patch: AppSettingsPatch,
  signal?: AbortSignal,
): Promise<AppSettingsDocument> {
  return apiFetch<AppSettingsDocument>('/api/settings', {
    method: 'PUT',
    body: JSON.stringify(patch),
    signal,
  })
}
