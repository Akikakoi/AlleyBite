/**
 * 分享图（海报）生成（文档 2.3 / 9.2：分享链接 / 保存图片）。
 *
 * 纯 Canvas 绘制，不引入 html2canvas 等第三方依赖，控制首屏体积（文档 9.4）。
 * 产出 PNG Blob，可下载或经 Web Share API 分享文件。
 */

import type { RankItem, RestaurantDetail } from '@/types'

import { formatPrice, formatScore } from './format'

const WIDTH = 750
const PAD = 44

const COLORS = {
  bg: '#f7f5f2',
  card: '#ffffff',
  primary: '#ff6b35',
  primarySoft: 'rgba(255, 107, 53, 0.12)',
  secondary: '#2f4f4f',
  text: '#26221f',
  sub: '#7a736c',
  border: 'rgba(38, 34, 31, 0.08)',
  good: '#3fa36b',
  goodSoft: 'rgba(63, 163, 107, 0.12)',
}

const FONT = "-apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', sans-serif"

interface DrawContext {
  ctx: CanvasRenderingContext2D
  canvas: HTMLCanvasElement
}

function createContext(height: number): DrawContext {
  const canvas = document.createElement('canvas')
  canvas.width = WIDTH
  canvas.height = Math.round(height)
  const ctx = canvas.getContext('2d')
  if (!ctx) throw new Error('当前环境不支持 Canvas')
  ctx.textBaseline = 'alphabetic'
  return { ctx, canvas }
}

function roundRect(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  r: number,
): void {
  const radius = Math.min(r, h / 2, w / 2)
  ctx.beginPath()
  ctx.moveTo(x + radius, y)
  ctx.arcTo(x + w, y, x + w, y + h, radius)
  ctx.arcTo(x + w, y + h, x, y + h, radius)
  ctx.arcTo(x, y + h, x, y, radius)
  ctx.arcTo(x, y, x + w, y, radius)
  ctx.closePath()
}

/** 按宽度折行；中英文混排按字符宽度粗略切分 */
function wrapText(ctx: CanvasRenderingContext2D, text: string, maxWidth: number): string[] {
  const lines: string[] = []
  let line = ''
  for (const char of text) {
    const next = line + char
    if (ctx.measureText(next).width > maxWidth && line) {
      lines.push(line)
      line = char
    } else {
      line = next
    }
  }
  if (line) lines.push(line)
  return lines
}

const BRAND = '苍蝇馆子'
const FOOTER_NOTE = '数据来源于公开信息，仅供参考，信息可能滞后'

function drawFooter(ctx: CanvasRenderingContext2D, y: number, url: string): number {
  ctx.fillStyle = COLORS.sub
  ctx.font = `22px ${FONT}`
  ctx.fillText(FOOTER_NOTE, PAD, y)
  ctx.fillText(url, PAD, y + 32)
  return y + 32
}

function drawTags(
  ctx: CanvasRenderingContext2D,
  tags: string[],
  x: number,
  y: number,
  maxWidth: number,
  limit = 4,
): number {
  const shown = tags.filter(Boolean).slice(0, limit)
  if (!shown.length) return y
  ctx.font = `22px ${FONT}`
  let cursorX = x
  let cursorY = y
  const tagH = 40
  const gap = 12
  for (const tag of shown) {
    const w = ctx.measureText(tag).width + 28
    if (cursorX + w > x + maxWidth) {
      cursorX = x
      cursorY += tagH + gap
    }
    roundRect(ctx, cursorX, cursorY, w, tagH, tagH / 2)
    ctx.fillStyle = COLORS.goodSoft
    ctx.fill()
    ctx.fillStyle = COLORS.good
    ctx.fillText(tag, cursorX + 14, cursorY + 27)
    cursorX += w + gap
  }
  return cursorY + tagH
}

function toBlob(canvas: HTMLCanvasElement): Promise<Blob> {
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => {
      if (blob) resolve(blob)
      else reject(new Error('分享图生成失败'))
    }, 'image/png')
  })
}

export interface RankPosterOptions {
  city: string
  items: RankItem[]
  baseUrl?: string
}

/** 榜单分享图：城市名 + Top N 店铺卡片 + 页脚（文档 9.2） */
export async function renderRankPoster({
  city,
  items,
  baseUrl = window.location.origin,
}: RankPosterOptions): Promise<Blob> {
  const shops = items.slice(0, 5)
  const headerH = 210
  const cardH = 168
  const footerH = 120
  const height = headerH + shops.length * (cardH + 16) + footerH + PAD

  const { ctx, canvas } = createContext(height)
  ctx.fillStyle = COLORS.bg
  ctx.fillRect(0, 0, WIDTH, height)

  // 头部
  ctx.fillStyle = COLORS.primary
  ctx.fillRect(0, 0, WIDTH, 14)
  ctx.fillStyle = COLORS.secondary
  ctx.font = `bold 52px ${FONT}`
  ctx.fillText(`${city}苍蝇馆子榜`, PAD, 96)
  ctx.fillStyle = COLORS.sub
  ctx.font = `26px ${FONT}`
  ctx.fillText('本地人才知道的宝藏小店 · 综合分排序', PAD, 142)
  ctx.fillStyle = COLORS.primarySoft
  roundRect(ctx, PAD, 158, 96, 36, 18)
  ctx.fill()
  ctx.fillStyle = COLORS.primary
  ctx.font = `bold 22px ${FONT}`
  ctx.fillText(BRAND, PAD + 16, 183)

  // 店铺卡片
  let y = headerH
  shops.forEach((item, index) => {
    roundRect(ctx, PAD, y, WIDTH - PAD * 2, cardH, 20)
    ctx.fillStyle = COLORS.card
    ctx.fill()

    // 排名徽标
    roundRect(ctx, PAD + 24, y + 28, 56, 56, 14)
    ctx.fillStyle = index < 3 ? COLORS.primary : 'rgba(47, 79, 79, 0.08)'
    ctx.fill()
    ctx.fillStyle = index < 3 ? '#ffffff' : COLORS.secondary
    ctx.font = `bold 28px ${FONT}`
    ctx.textAlign = 'center'
    ctx.fillText(String(item.rank), PAD + 24 + 28, y + 66)
    ctx.textAlign = 'left'

    const textX = PAD + 102
    const textW = WIDTH - PAD * 2 - 102 - 24
    ctx.fillStyle = COLORS.text
    ctx.font = `bold 32px ${FONT}`
    const nameLines = wrapText(ctx, item.name, textW - 120)
    ctx.fillText(nameLines[0], textX, y + 62)

    ctx.fillStyle = COLORS.primary
    ctx.font = `bold 32px ${FONT}`
    ctx.textAlign = 'right'
    ctx.fillText(formatScore(item.score), WIDTH - PAD - 24, y + 62)
    ctx.textAlign = 'left'

    ctx.fillStyle = COLORS.sub
    ctx.font = `24px ${FONT}`
    const meta = [item.area, formatPrice(item.avg_price)].filter(Boolean).join(' · ')
    ctx.fillText(meta, textX, y + 100)

    drawTags(ctx, item.praise_keywords, textX, y + 118, textW, 3)
    y += cardH + 16
  })

  drawFooter(ctx, height - 70, baseUrl)
  return toBlob(canvas)
}

export interface ShopPosterOptions {
  detail: RestaurantDetail
  baseUrl?: string
}

/** 店铺分享图：单店卡片 + 地址 + 免责声明（文档 9.2） */
export async function renderShopPoster({
  detail,
  baseUrl = window.location.origin,
}: ShopPosterOptions): Promise<Blob> {
  const headerH = 250
  const blockH = 150
  const height = headerH + blockH * 2 + 240

  const { ctx, canvas } = createContext(height)
  ctx.fillStyle = COLORS.bg
  ctx.fillRect(0, 0, WIDTH, height)
  ctx.fillStyle = COLORS.primary
  ctx.fillRect(0, 0, WIDTH, 14)

  // 头部：店名 + 综合分
  ctx.fillStyle = COLORS.text
  ctx.font = `bold 48px ${FONT}`
  const nameLines = wrapText(ctx, detail.name, WIDTH - PAD * 2)
  ctx.fillText(nameLines[0], PAD, 100)

  ctx.fillStyle = COLORS.sub
  ctx.font = `26px ${FONT}`
  const subline = [detail.area, detail.cuisine, formatPrice(detail.avg_price)]
    .filter(Boolean)
    .join(' · ')
  ctx.fillText(subline, PAD, 146)

  ctx.fillStyle = COLORS.primarySoft
  roundRect(ctx, PAD, 176, 230, 72, 20)
  ctx.fill()
  ctx.fillStyle = COLORS.primary
  ctx.font = `bold 40px ${FONT}`
  ctx.fillText(formatScore(detail.score), PAD + 24, 226)
  ctx.fillStyle = COLORS.sub
  ctx.font = `22px ${FONT}`
  ctx.fillText('综合分', PAD + 118, 226)

  // 推荐菜
  let y = headerH
  if (detail.recommended_dishes.length) {
    ctx.fillStyle = COLORS.secondary
    ctx.font = `bold 26px ${FONT}`
    ctx.fillText('推荐菜', PAD, y)
    drawTags(ctx, detail.recommended_dishes, PAD, y + 20, WIDTH - PAD * 2, 6)
  }
  y += blockH

  // 口碑关键词
  const keywords = [...detail.praise_keywords, ...detail.complaints]
  if (keywords.length) {
    ctx.fillStyle = COLORS.secondary
    ctx.font = `bold 26px ${FONT}`
    ctx.fillText('口碑关键词', PAD, y)
    drawTags(ctx, keywords, PAD, y + 20, WIDTH - PAD * 2, 8)
  }
  y += blockH

  // 地址
  ctx.fillStyle = COLORS.secondary
  ctx.font = `bold 26px ${FONT}`
  ctx.fillText('地址', PAD, y)
  ctx.fillStyle = COLORS.text
  ctx.font = `24px ${FONT}`
  const addrLines = wrapText(ctx, detail.address || '地址待补充', WIDTH - PAD * 2)
  addrLines.slice(0, 2).forEach((line, i) => ctx.fillText(line, PAD, y + 36 + i * 34))

  drawFooter(ctx, height - 70, baseUrl)
  return toBlob(canvas)
}

/** 触发浏览器下载（分享图另存为） */
export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

/** 优先经系统分享图片文件；不支持时降级为下载 */
export async function shareOrDownloadImage(
  blob: Blob,
  filename: string,
  text: string,
): Promise<void> {
  const file = new File([blob], filename, { type: 'image/png' })
  const nav = navigator as Navigator & {
    canShare?: (data: ShareData) => boolean
    share?: (data: ShareData) => Promise<void>
  }
  if (nav.share && nav.canShare?.({ files: [file] })) {
    try {
      await nav.share({ files: [file], text })
      return
    } catch (error) {
      if ((error as DOMException | undefined)?.name === 'AbortError') return
    }
  }
  downloadBlob(blob, filename)
}