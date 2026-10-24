/** 来源链接的桌面版映射（证据链原链接多为移动端页面，电脑上偏窄）。
 * 只收录有把握同路径对得上桌面版的站点，其余返回 null 不显示切换入口。 */
const HOST_RULES: Array<{ host: string; pathPrefix?: string; desktop: string }> = [
  { host: 'm.toutiao.com', pathPrefix: '/article/', desktop: 'www.toutiao.com' },
  { host: 'm.sohu.com', pathPrefix: '/a/', desktop: 'www.sohu.com' },
  { host: 'm.thepaper.cn', desktop: 'www.thepaper.cn' },
  { host: 'm.163.com', pathPrefix: '/dy/article/', desktop: 'www.163.com' },
]

export function desktopUrl(url: string): string | null {
  if (!url) return null
  let parsed: URL
  try {
    parsed = new URL(url)
  } catch {
    return null
  }
  if (parsed.protocol !== 'https:' && parsed.protocol !== 'http:') return null

  const host = parsed.hostname.toLowerCase()
  for (const rule of HOST_RULES) {
    if (host !== rule.host) continue
    if (rule.pathPrefix && !parsed.pathname.startsWith(rule.pathPrefix)) continue
    parsed.hostname = rule.desktop
    return parsed.toString()
  }
  return null
}
