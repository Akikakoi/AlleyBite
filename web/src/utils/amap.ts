/**
 * 高德 JS API 动态加载（文档 2.2 V1.1 地图模式 / US-07）。
 *
 * 通过 `VITE_AMAP_JS_KEY`（+ 可选 `VITE_AMAP_SECURITY_CODE`）在运行时按需加载，
 * 未配置时不发起请求，由调用方降级为「导航列表」。避免把无法验证的外部脚本
 * 写死进首屏，同时不引入 npm 依赖。
 */

export interface AMapMap {
  add(overlay: unknown): void
  remove(overlay: unknown): void
  clearMap(): void
  setFitView(): void
  setCenter(position: [number, number]): void
  setZoom(zoom: number): void
  destroy(): void
}

export interface AMapMarker {
  setMap(map: AMapMap | null): void
  on(event: string, handler: () => void): void
}

export interface AMapInfoWindow {
  open(map: AMapMap, position: [number, number]): void
  close(): void
  setContent(content: string): void
}

export interface AMapNamespace {
  Map: new (
    container: HTMLElement | string,
    options?: Record<string, unknown>,
  ) => AMapMap
  Marker: new (options?: Record<string, unknown>) => AMapMarker
  InfoWindow: new (options?: Record<string, unknown>) => AMapInfoWindow
}

declare global {
  interface Window {
    AMap?: AMapNamespace
    _AMapSecurityConfig?: { securityJsCode?: string }
  }
}

const JS_KEY = import.meta.env.VITE_AMAP_JS_KEY
const SECURITY_CODE = import.meta.env.VITE_AMAP_SECURITY_CODE

/** 是否配置了高德 JS key；未配置时地图模式走降级视图 */
export const amapEnabled = Boolean(JS_KEY && JS_KEY.trim())

let pending: Promise<AMapNamespace> | null = null

/** 按需加载高德 JS API；重复调用复用同一 Promise */
export function loadAmap(): Promise<AMapNamespace> {
  if (window.AMap) return Promise.resolve(window.AMap)
  if (!amapEnabled) return Promise.reject(new Error('未配置 VITE_AMAP_JS_KEY'))
  if (pending) return pending

  pending = new Promise<AMapNamespace>((resolve, reject) => {
    if (SECURITY_CODE) {
      window._AMapSecurityConfig = { securityJsCode: SECURITY_CODE }
    }
    const script = document.createElement('script')
    script.src = `https://webapi.amap.com/maps?v=2.0&key=${encodeURIComponent(JS_KEY ?? '')}`
    script.async = true
    script.onload = () => {
      if (window.AMap) resolve(window.AMap)
      else reject(new Error('高德地图加载失败'))
    }
    script.onerror = () => {
      pending = null
      reject(new Error('高德地图加载失败'))
    }
    document.head.appendChild(script)
  })
  return pending
}

/** 高德导航链接（文档 9.2 一键导航）：有坐标走打点，否则走关键词搜索 */
export function amapNavigationUrl(
  address: string,
  location?: { lat: number; lng: number } | null,
): string {
  if (location) {
    const name = encodeURIComponent(address || '目的地')
    return `https://uri.amap.com/marker?position=${location.lng},${location.lat}&name=${name}`
  }
  return `https://uri.amap.com/search?keyword=${encodeURIComponent(address)}`
}