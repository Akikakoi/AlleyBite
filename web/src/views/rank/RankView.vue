<template>
  <div class="page rank">
    <header class="rank__head">
      <button class="rank__back press-feedback" aria-label="返回" @click="router.back()">‹</button>
      <div class="rank__title">
        <h1 class="page-title">{{ city }}</h1>
        <p class="text-sub">苍蝇馆子榜 · 只收单店小店</p>
        <p v-if="state === 'ready' && cityRankCount" class="text-sub rank__meta">
          {{ metaLine }}
        </p>
      </div>
      <div class="rank__actions">
        <van-button size="small" round plain @click="toMap">地图</van-button>
        <van-button size="small" round plain type="primary" @click="showShare = true">
          分享
        </van-button>
      </div>
    </header>

    <FilterBar
      :filters="filters"
      :cuisines="cuisines"
      :areas="areas"
      @change="onFilterChange"
    />

    <EmptyState
      v-if="state === 'collecting'"
      title="这座城市正在收录中"
      desc="开城后会第一时间更新榜单，先收藏本站吧"
    >
      <template #action>
        <van-button type="primary" round @click="router.push('/')">换个城市</van-button>
      </template>
    </EmptyState>

    <EmptyState v-else-if="state === 'error'" title="榜单加载失败" :desc="errorMessage">
      <template #action>
        <van-button type="primary" round @click="reload">重新加载</van-button>
      </template>
    </EmptyState>

    <template v-else>
      <div
        v-if="items.length"
        class="list-grid"
        :style="{ opacity: loading ? 0.55 : 1 }"
      >
        <ShopCard
          v-for="(item, idx) in items"
          :key="item.restaurant_id"
          :item="item"
          :style="{ '--stagger-delay': Math.min(idx * 30, 240) + 'ms' }"
          @click="openDetail(item)"
        />
      </div>
      <SkeletonGrid v-else-if="loading" />
      <EmptyState
        v-else-if="!loading"
        title="没有符合筛选的店铺"
        desc="试试放宽菜系、人均或区域条件"
      />
      <!-- 自绘分页条：首页/末页页码常驻，中间以省略号截断 -->
      <nav v-if="totalPages > 1" class="pager" aria-label="榜单分页">
        <button
          type="button"
          class="pager__item pager__nav press-feedback"
          :disabled="currentPage === 1"
          @click="goPage(currentPage - 1)"
        >‹</button>
        <template v-for="(p, i) in pageItems" :key="`${p}-${i}`">
          <span v-if="p === '…'" class="pager__item pager__gap">…</span>
          <button
            v-else
            type="button"
            class="pager__item"
            :class="{ 'pager__item--active': p === currentPage, 'press-feedback': p !== currentPage }"
            @click="goPage(p as number)"
          >{{ p }}</button>
        </template>
        <button
          type="button"
          class="pager__item pager__nav press-feedback"
          :disabled="currentPage === totalPages"
          @click="goPage(currentPage + 1)"
        >›</button>
      </nav>
      <!-- 收录口径（产品定位 2026-10-23）：把标准写在明面上，用户可据此判断榜单边界 -->
      <p class="text-sub rank__standard">
        收录口径：本地单店小店，连锁与已歇业店不进榜；每条口碑均标注原文来源。
      </p>
    </template>

    <ShareSheet
      v-model:show="showShare"
      :title="`${city}苍蝇馆子榜`"
      text="本地人才知道的宝藏小店"
      :url="shareUrl()"
      :filename="`${city}苍蝇馆子榜.png`"
      :poster="rankPoster"
      kind="rank"
      :city="city"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { getRank } from '@/api/rank'
import { ApiError, isNotFound } from '@/api/request'
import EmptyState from '@/components/EmptyState.vue'
import FilterBar from '@/components/FilterBar.vue'
import SkeletonGrid from '@/components/SkeletonGrid.vue'
import ShareSheet from '@/components/ShareSheet.vue'
import ShopCard from '@/components/ShopCard.vue'
import { useCityStore } from '@/store/city'
import type { RankFilters, RankItem } from '@/types'
import { renderRankPoster } from '@/utils/shareImage'
import { track } from '@/utils/track'

/** 文档 9.2：榜单分页展示，每页 15 条 */
const PAGE_SIZE = 15

const route = useRoute()
const router = useRouter()
const store = useCityStore()

const city = ref(String(route.query.city || store.current || ''))
if (city.value) store.setCity(city.value)

const filters = ref<RankFilters>(readFilters())
const items = ref<RankItem[]>([])
const currentPage = ref(1)
const total = ref(0)
const loading = ref(false)
const state = ref<'ready' | 'collecting' | 'error'>('ready')
const errorMessage = ref('')
const showShare = ref(false)
/** 城市覆盖度（产品定位 2026-10-23）：条数取最新快照全量，不受筛选影响 */
const cityRankCount = ref(0)
const maturity = ref<string>('collecting')
const generatedAt = ref<string | null>(null)
const MATURITY_LABELS: Record<string, string> = {
  deep: '深度覆盖',
  initial: '初步覆盖',
  collecting: '少量收录',
}
const metaLine = computed(() => {
  const parts = [`${cityRankCount.value} 家上榜`]
  parts.push(MATURITY_LABELS[maturity.value] ?? '少量收录')
  if (generatedAt.value) {
    const d = new Date(generatedAt.value)
    if (!Number.isNaN(d.getTime())) {
      parts.push(`更新 ${d.getMonth() + 1}-${String(d.getDate()).padStart(2, '0')}`)
    }
  }
  return parts.join(' · ')
})

/** 筛选选项跨页累积：翻页后下拉选项不丢失 */
const cuisines = ref<string[]>([])
const areas = ref<string[]>([])

function mergeOptions(list: RankItem[]) {
  const cuisineSet = new Set(cuisines.value)
  const areaSet = new Set(areas.value)
  list.forEach((item) => {
    if (item.cuisine) cuisineSet.add(item.cuisine)
    if (item.area) areaSet.add(item.area)
  })
  cuisines.value = Array.from(cuisineSet)
  areas.value = Array.from(areaSet)
}

if (!city.value) {
  router.replace('/')
}

// 原 van-list 挂载时自动触发首次加载，改分页后需显式调用
onMounted(() => {
  // 带走率分母（方向六）：榜单页曝光按会话去重
  if (city.value) track('rank_view', { city: city.value })
  loadPage()
})

function readFilters(): RankFilters {
  const q = route.query
  const priceMin = q.price_min ? Number(q.price_min) : undefined
  const priceMax = q.price_max ? Number(q.price_max) : undefined
  return {
    cuisine: q.cuisine ? String(q.cuisine) : undefined,
    area: q.area ? String(q.area) : undefined,
    priceMin,
    priceMax,
    days: q.days ? Number(q.days) : undefined,
  }
}

function reset() {
  items.value = []
  currentPage.value = 1
  total.value = 0
  cityRankCount.value = 0
  generatedAt.value = null
  cuisines.value = []
  areas.value = []
  state.value = 'ready'
}

/** 请求序号：快速连点时丢弃过期响应，避免旧页覆盖新页（表现为"点了没反应"） */
let requestSeq = 0

async function loadPage() {
  if (!city.value) {
    loading.value = false
    return
  }
  const seq = ++requestSeq
  loading.value = true
  try {
    const data = await getRank({
      city: city.value,
      page: currentPage.value,
      pageSize: PAGE_SIZE,
      ...filters.value,
    })
    if (seq !== requestSeq) return
    items.value = data.items
    total.value = data.total
    cityRankCount.value = data.city_rank_count ?? data.total
    maturity.value = data.maturity ?? 'collecting'
    generatedAt.value = data.generated_at
    mergeOptions(data.items)
    state.value = 'ready'
  } catch (err) {
    if (seq !== requestSeq) return
    if (isNotFound(err)) {
      state.value = 'collecting'
    } else {
      state.value = 'error'
      errorMessage.value =
        err instanceof ApiError ? err.message : '加载失败，请稍后重试'
    }
    items.value = []
  } finally {
    if (seq === requestSeq) loading.value = false
  }
}

function reload() {
  reset()
  loadPage()
}

/** 总页数；分页条页码序列：首页/末页常驻，当前页±1，间隔以省略号截断 */
const totalPages = computed(() => Math.ceil(total.value / PAGE_SIZE))
const pageItems = computed<(number | '…')[]>(() => {
  const last = totalPages.value
  const cur = currentPage.value
  if (last <= 7) return Array.from({ length: last }, (_, i) => i + 1)
  const wanted = new Set<number>([1, last, cur - 1, cur, cur + 1])
  const pages = [...wanted].filter((n) => n >= 1 && n <= last).sort((a, b) => a - b)
  const items: (number | '…')[] = []
  let prev = 0
  for (const p of pages) {
    if (p - prev > 1) items.push('…')
    items.push(p)
    prev = p
  }
  return items
})

function goPage(page: number) {
  if (page < 1 || page > totalPages.value || page === currentPage.value) return
  currentPage.value = page
  loadPage()
  // 动效无障碍：reduce 模式下直跳，不做平滑滚动
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  window.scrollTo({ top: 0, behavior: reduceMotion ? 'auto' : 'smooth' })
}

function onFilterChange(next: RankFilters) {
  filters.value = next
  const query: Record<string, string> = { city: city.value }
  if (next.cuisine) query.cuisine = next.cuisine
  if (next.area) query.area = next.area
  if (next.priceMin !== undefined) query.price_min = String(next.priceMin)
  if (next.priceMax !== undefined) query.price_max = String(next.priceMax)
  if (next.days !== undefined) query.days = String(next.days)
  router.replace({ path: '/rank', query })
  reload()
}

function openDetail(item: RankItem) {
  router.push({ path: `/detail/${item.restaurant_id}` })
}

function toMap() {
  router.push({ path: '/map', query: { ...route.query } })
}

/** 榜单分享图：取当前已加载的 Top5 + 诚实三要素（全榜条数/成熟度/截至日期，方向六批 1） */
function rankPoster() {
  return renderRankPoster({
    city: city.value,
    items: items.value,
    count: cityRankCount.value,
    maturity: maturity.value,
    updatedAt: generatedAt.value,
    baseUrl: window.location.origin,
  })
}

/** 分享链接带城市与筛选参数（文档 9.2），保证打开后定位到同一榜单 */
function shareUrl(): string {
  const query: Record<string, string> = { city: city.value }
  const f = filters.value
  if (f.cuisine) query.cuisine = f.cuisine
  if (f.area) query.area = f.area
  if (f.priceMin !== undefined) query.price_min = String(f.priceMin)
  if (f.priceMax !== undefined) query.price_max = String(f.priceMax)
  if (f.days !== undefined) query.days = String(f.days)
  return `${window.location.origin}/rank?${new URLSearchParams(query).toString()}`
}
</script>

<style scoped>
.rank {
  padding-top: 16px;
}

.list-grid {
  transition: opacity var(--dur-ui) var(--ease-out);
}

/* 榜单卡片入场：淡入上浮按序错峰（--stagger-delay 由模板按 index 注入，上限 240ms）；
   reduce 下全局降级块把 animation-duration 压到 ≈0 且 animation-delay 归零，卡片直出 */
.list-grid > * {
  animation: rank-rise var(--dur-ui) var(--ease-out) backwards;
  animation-delay: var(--stagger-delay, 0ms);
}

@keyframes rank-rise {
  from {
    opacity: 0;
    transform: translateY(8px);
  }
}

.rank__head {
  display: flex;
  align-items: center;
  gap: 12px;
}

.rank__back {
  flex: none;
  width: 32px;
  height: 32px;
  border: none;
  border-radius: 50%;
  background: var(--color-surface);
  box-shadow: var(--shadow-card);
  font-size: 20px;
  line-height: 1;
  cursor: pointer;
}

.rank__title {
  flex: 1;
  min-width: 0;
}

.rank__title .page-title {
  font-size: 20px;
}

.rank__title .text-sub {
  margin: 2px 0 0;
}

/* 覆盖度/更新时间：第三行弱化展示，避免与标题抢视觉 */
.rank__meta {
  font-size: 11px;
  opacity: 0.75;
}

/* 收录口径说明：列表末尾的诚实边界声明 */
.rank__standard {
  margin: 16px 4px 0;
  font-size: 11px;
  line-height: 1.6;
  opacity: 0.7;
}

.rank__actions {
  flex: none;
  display: flex;
  gap: 8px;
}

/* 分页条：首页/末页常驻，胶囊圆角、暖橙高亮，与站点风格统一（自绘，无 Vant） */
.pager {
  display: flex;
  justify-content: center;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin: 24px 0 8px;
}

.pager__item {
  min-width: 36px;
  height: 36px;
  padding: 0 10px;
  border-radius: var(--radius-tag);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  color: var(--color-secondary);
  font-size: var(--font-body);
  font-weight: 500;
  line-height: 34px;
  text-align: center;
  cursor: pointer;
  /* transform 必须写在 scoped 声明里：scoped 样式注入晚于全局 .press-feedback，
     平局特异度下本规则的 transition 会整条覆盖全局类，漏写则按压缩放瞬跳 */
  transition:
    transform var(--dur-press) var(--ease-out),
    background-color var(--dur-ui) var(--ease-out),
    color var(--dur-ui) var(--ease-out),
    border-color var(--dur-ui) var(--ease-out);
}

.pager__item:hover:not(.pager__item--active):not(:disabled):not(.pager__gap) {
  background: var(--color-primary-soft);
  border-color: transparent;
  color: var(--color-primary);
}

.pager__item--active {
  background: var(--color-primary);
  border-color: var(--color-primary);
  color: var(--color-surface-solid);
  font-weight: 700;
  box-shadow: 0 4px 12px rgba(255, 107, 53, 0.28);
  cursor: default;
}

.pager__item:disabled {
  background: transparent;
  border-color: transparent;
  color: rgba(122, 115, 108, 0.4);
  cursor: default;
}

.pager__nav {
  width: 36px;
  padding: 0;
  font-size: 22px;
  line-height: 34px;
}

.pager__gap {
  background: transparent;
  border-color: transparent;
  cursor: default;
  min-width: 24px;
  padding: 0;
}
</style>