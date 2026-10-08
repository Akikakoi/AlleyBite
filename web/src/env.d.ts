/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE: string
  /** 高德 JS API（地图模式，V1.1）；留空则地图降级为占位 + 导航列表 */
  readonly VITE_AMAP_JS_KEY?: string
  /** 高德 JS API 安全密钥（2021-12 后申请的 key 需配套）；留空则不设置 */
  readonly VITE_AMAP_SECURITY_CODE?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}