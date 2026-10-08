<template>
  <div class="page map">
    <header class="map__head">
      <button class="map__back" aria-label="返回" @click="router.back()">‹</button>
      <div class="map__title">
        <h1 class="page-title">{{ city }}</h1>
        <p class="text-sub">苍蝇馆子榜 · 地图模式</p>
      </div>
      <van-button size="small" round plain type="primary" @click="toList">列表</van-button>
    </header>

    <EmptyState v-if="!amapEnabled" title="地图模式未启用" desc="未配置高德地图 Key，以下为带坐标的店铺，可一键导航" />

    <template v-else>
      <EmptyState v-if="state === 'loading'" title="地图加载中…" />
      <EmptyState v-else-if="state === 'error'" title="地图加载失败" :desc="errorMessage">
        <template #action>
          <van-button type="primary" round @click="reload">重新加载</van-button>
        </template>
      </EmptyState>
      <template v-else>
        <div ref="containerRef" class="map__canvas" />
        <p class="text-sub map__hint">共 {{ locatedItems.length }} 家带坐标的店铺，点击标记查看详情</p>

        <section v-if="selected" class="card map__popup">
          <div class="map__popup-head">
            <h3 class="map__popup-name">{{ selected.name }}</h3>
            <span class="map__popup-score">{{ formatScore(selected.score) }}</span>
          </div>
          <p class="text-sub map__popup-meta">
            {{ [selected.area, formatPrice(selected.avg_price)].filter(Boolean).join(' · ') }}
          </p>
          <p v-if="selected.address" class="text-sub map__popup-addr">{{ selected.address }}</p>
          <div class="map__popup-actions">
            <van-button size="small" round type="primary" @click="openDetail(selected)">
              查看详情
            </van-button>
            <van-button size="small" round plain @click="navigate(selected)">导航</van-button>
          </div>
        </section>
      </template>
    </template>

    <div v-if="!amapEnabled && locatedItems.length" class="list-grid map__fallback">
      <ShopCard
        v-for="item in locatedItems"
        :key="item.restaurant_id"
        :item="item"
        @click="openDetail(item)"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { getRank } from '@/api/rank'
import { ApiError, isNotFound } from '@/api/request'
import EmptyState from '@/components/EmptyState.vue'
import ShopCard from '@/components/ShopCard.vue'
import { useCityStore } from '@/store/city'
import type { RankFilters, RankItem } from '@/types'
import { amapEnabled, amapNavigationUrl, loadAmap, type AMapMap } from '@/utils/amap'
import { formatPrice, formatScore } from '@/utils/format'

const PAGE_SIZE = 20
const MAX_PAGES = 4

const route = useRoute()
const router = useRouter()
const store = useCityStore()

const city = ref(String(route.query.city || store.current || ''))
if (city.value) store.setCity(city.value)

const filters = ref<RankFilters>(readFilters())
const items = ref<RankItem[]>([])
const state = ref<'loading' | 'ready' | 'error'>('loading')
const errorMessage = ref('')
const selected = ref<RankItem | null>(null)

const containerRef = ref<HTMLDivElement | null>(null)
let map: AMapMap | null = null

const locatedItems = computed(() => items.value.filter((item) => item.location))

if (!city.value) {
  router.replace('/')
}

function readFilters(): RankFilters {
  const q = route.query
  return {
    cuisine: q.cuisine ? String(q.cuisine) : undefined,
    area: q.area ? String(q.area) : undefined,
    priceMin: q.price_min ? Number(q.price_min) : undefined,
    priceMax: q.price_max ? Number(q.price_max) : undefined,
    days: q.days ? Number(q.days) : undefined,
  }
}

async function fetchItems() {
  const collected: RankItem[] = []
  for (let page = 1; page <= MAX_PAGES; page += 1) {
    const data = await getRank({
      city: city.value,
      page,
      pageSize: PAGE_SIZE,
      ...filters.value,
    })
    collected.push(...data.items)
    if (collected.length >= data.total || data.items.length === 0) break
  }
  items.value = collected
}

async function initMap() {
  const AMap = await loadAmap()
  if (!containerRef.value) return
  const located = locatedItems.value
  const first = located[0]?.location
  map = new AMap.Map(containerRef.value, {
    zoom: 11,
    center: first ? [first.lng, first.lat] : [104.066, 30.572],
  })
  located.forEach((item) => {
    if (!item.location) return
    const marker = new AMap.Marker({
      position: [item.location.lng, item.location.lat],
      title: item.name,
    })
    marker.on('click', () => {
      selected.value = item
    })
    map?.add(marker)
  })
  map.setFitView()
}

async function reload() {
  state.value = 'loading'
  selected.value = null
  try {
    await fetchItems()
    if (amapEnabled) await initMap()
    state.value = 'ready'
  } catch (err) {
    if (isNotFound(err)) {
      state.value = 'ready'
      items.value = []
      return
    }
    state.value = 'error'
    errorMessage.value = err instanceof ApiError ? err.message : '加载失败，请稍后重试'
  }
}

function openDetail(item: RankItem) {
  router.push(`/detail/${item.restaurant_id}`)
}

function navigate(item: RankItem) {
  const target = item.address || item.name
  window.open(amapNavigationUrl(target, item.location), '_blank', 'noopener')
}

function toList() {
  router.push({ path: '/rank', query: { ...route.query } })
}

onMounted(reload)

onBeforeUnmount(() => {
  map?.destroy()
  map = null
})
</script>

<style scoped>
.map {
  padding-top: 16px;
}

.map__head {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}

.map__back {
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

.map__title {
  flex: 1;
  min-width: 0;
}

.map__title .page-title {
  font-size: 20px;
}

.map__title .text-sub {
  margin: 2px 0 0;
}

.map__canvas {
  width: 100%;
  height: 58vh;
  min-height: 340px;
  border-radius: var(--radius-card);
  overflow: hidden;
  box-shadow: var(--shadow-card);
}

.map__hint {
  margin: 8px 0 0;
}

.map__popup {
  margin-top: 12px;
  padding: 16px;
}

.map__popup-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}

.map__popup-name {
  margin: 0;
  font-size: var(--font-title);
  font-weight: 700;
}

.map__popup-score {
  color: var(--color-primary);
  font-weight: 700;
}

.map__popup-meta,
.map__popup-addr {
  margin: 4px 0 0;
}

.map__popup-actions {
  display: flex;
  gap: 10px;
  margin-top: 12px;
}

.map__fallback {
  margin-top: 16px;
}
</style>