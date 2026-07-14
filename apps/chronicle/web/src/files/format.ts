/** Human-readable byte sizes for the files table. */
export function formatBytes(size: number | null | undefined): string {
  if (size == null || !Number.isFinite(size)) return '—'
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  if (size < 1024 * 1024 * 1024) return `${(size / (1024 * 1024)).toFixed(1)} MB`
  return `${(size / (1024 * 1024 * 1024)).toFixed(1)} GB`
}

/** Map content-type to family label for display. */
export function contentTypeFamily(ct: string | null | undefined): string {
  if (!ct) return 'other'
  const lower = ct.toLowerCase()
  if (lower.startsWith('application/pdf')) return 'pdf'
  if (lower.startsWith('image/')) return 'image'
  if (
    lower.includes('spreadsheet') ||
    lower.includes('ms-excel') ||
    lower.startsWith('text/csv')
  ) {
    return 'spreadsheet'
  }
  if (
    lower.includes('wordprocessing') ||
    lower.includes('msword') ||
    lower.includes('presentation') ||
    lower.startsWith('text/html')
  ) {
    return 'document'
  }
  if (lower.startsWith('text/plain')) return 'text'
  return 'other'
}

export function isImageFamily(ct: string | null | undefined): boolean {
  return contentTypeFamily(ct) === 'image'
}

export function previewUrl(attSid: string): string {
  return `/api/attachments/${encodeURIComponent(attSid)}/preview`
}

export function downloadUrl(attSid: string): string {
  return `/api/attachments/${encodeURIComponent(attSid)}/download`
}

export function truncateFilename(name: string, max = 40): string {
  if (name.length <= max) return name
  const extIdx = name.lastIndexOf('.')
  if (extIdx > 0 && name.length - extIdx <= 8) {
    const ext = name.slice(extIdx)
    const base = name.slice(0, max - ext.length - 1)
    return `${base}…${ext}`
  }
  return `${name.slice(0, max - 1)}…`
}
