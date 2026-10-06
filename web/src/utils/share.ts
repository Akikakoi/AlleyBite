import { showToast } from 'vant'

interface ShareOptions {
  title: string
  text?: string
  url: string
}

/** 优先 Web Share API，降级为复制链接（文档 9.2 榜单页分享） */
export async function shareLink({ title, text, url }: ShareOptions): Promise<void> {
  if (typeof navigator.share === 'function') {
    try {
      await navigator.share({ title, text, url })
      return
    } catch (error) {
      // 用户主动取消分享时静默，不再降级复制
      if ((error as DOMException | undefined)?.name === 'AbortError') return
    }
  }

  try {
    await navigator.clipboard.writeText(url)
    showToast('链接已复制')
  } catch {
    showToast('复制失败，请手动复制地址栏链接')
  }
}