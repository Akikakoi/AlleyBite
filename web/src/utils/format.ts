export function formatPrice(value: number | null | undefined): string {
  if (value === null || value === undefined) return '人均待补充'
  return `人均 ¥${Math.round(value)}`
}

export function formatScore(value: number): string {
  return Number(value).toFixed(1)
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return ''
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  return `${date.getMonth() + 1}月${date.getDate()}日`
}

const SOURCE_LABELS: Record<string, string> = {
  dianping: '大众点评',
  xiaohongshu: '小红书',
  forum: '本地论坛',
  weibo: '微博',
  map: '地图',
  unknown: '公开信息',
}

export function sourceLabel(source: string | null | undefined): string {
  if (!source) return '公开信息'
  return SOURCE_LABELS[source] ?? source
}