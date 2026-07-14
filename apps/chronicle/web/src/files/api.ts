import { apiGet } from '../api/client'
import type {
  AttachmentCompareResponse,
  AttachmentFamilyResponse,
} from './types'

export function getAttachmentFamily(
  attSid: string,
  signal?: AbortSignal,
): Promise<AttachmentFamilyResponse> {
  return apiGet<AttachmentFamilyResponse>(
    `/api/attachments/${encodeURIComponent(attSid)}/family`,
    signal,
  )
}

export function getAttachmentCompare(
  a: string,
  b: string,
  signal?: AbortSignal,
): Promise<AttachmentCompareResponse> {
  const sp = new URLSearchParams({ a, b })
  return apiGet<AttachmentCompareResponse>(
    `/api/attachments/compare?${sp}`,
    signal,
  )
}
