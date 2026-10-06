export function prettyJson(value: unknown): string {
  if (value === null || value === undefined) return '-'
  if (typeof value === 'string') {
    const text = value.trim()
    if (!text) return '-'
    try {
      return JSON.stringify(JSON.parse(text), null, 2)
    } catch {
      return value
    }
  }
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

export function formatTime(value: string | null | undefined): string {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', { hour12: false })
}