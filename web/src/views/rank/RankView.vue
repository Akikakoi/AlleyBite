<template>
  <div class="page rank">
    <header class="rank__head">
      <button class="rank__back" aria-label="返回" @click="router.back()">‹</button>
      <div class="rank__title">
        <h1 class="page-title">{{ city }}</h1>
        <p class="text-sub">苍蝇馆子榜 · 综合分排序</p>
      </div>
      <van-button size="small" round plain type="primary" @click="onShare">
        分享
      </van-button>
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
      <div v-if="items.length" class="list-grid">
        <ShopCard
          v-for="item in items"
          :key="item.restaurant_id"
          :item="item"
          @click="openDetail(item)"
        />
      </div>
      <EmptyState
        v-else-if="finished"
        title="没有符合筛选的店铺"
        desc="试试放宽菜系、人均或区域条件"
      />
      <van-list
        v-model:loading="loading"
        :finished="finished"
        :finished-text="items.length ? '没有更多了' : ''"
        @load="onLoad"
      />
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { getRank } from '@/api/rank'
import { ApiError, isNotFound } from '@/api/request'
import EmptyState from '@/components/EmptyState.vue'
import FilterBar from '@/components/FilterBar.vue'
import ShopCard from '@/components/ShopCard.vue'
import { useCityStore } from '@/store/city'
import type { RankFilters, RankItem } from '@/types'
import { shareLink } from '@/utils/share'

const PAGE_SIZE = 20

const route = useRoute()
const router = useRouter()
const store = useCityStore()

const city = ref(String(route.query.city || store.current || ''))
if (city.value) store.setCity(city.value)

const filters = ref<RankFilters>(readFilters())
const items = ref<RankItem[]>([])
const page = ref(1)
const loading = ref(false)
const finished = ref(false)
const state = ref<'ready' | 'collecting' | 'error'>('ready')
const errorMessage = ref('')

const cuisines = computed(() =>
  Array.from(new Set(items.value.map((i) => i.cuisine).filter(Boolean) as string[])),
)
const areas = computed(() =>
  Array.from(new Set(items.value.map((i) => i.area).filter(Boolean) as string[])),
)

if (!city.value) {
  router.replace('/')
}

function readFilters(): RankFilters {
  const q = route.query
  const priceMin = q.price_min ? Number(q.price_min) : undefined
  const priceMax = q.price_max ? Number(q.price_max) : undefined
  return {
    cuisine: q.cuisine ? String(q.cuisine) : undefined,
    area: q.area ? String(q.area) : undefined,
    priceMin,
    priceMax,
  }
}

function reset() {
  items.value = []
  page.value = 1
  finished.value = false
  state.value = 'ready'
}

async function onLoad() {
  if (!city.value) {
    loading.value = false
    finished.value = true
    return
  }
  try {
    const data = await getRank({
      city: city.value,
      page: page.value,
      pageSize: PAGE_SIZE,
      ...filters.value,
    })
    if (page.value === 1) items.value = []
    items.value.push(...data.items)
    if (items.value.length >= data.total || data.items.length === 0) {
      finished.value = true
    } else {
      page.value += 1
    }
    state.value = 'ready'
  } catch (err) {
    if (isNotFound(err)) {
      state.value = 'collecting'
    } else {
      state.value = 'error'
      errorMessage.value =
        err instanceof ApiError ? err.message : '加载失败，请稍后重试'
    }
    finished.value = true
  } finally {
    loading.value = false
  }
}

function reload() {
  reset()
  loading.value = true
  onLoad()
}

function onFilterChange(next: RankFilters) {
  filters.value = next
  const query: Record<string, string> = { city: city.value }
  if (next.cuisine) query.cuisine = next.cuisine
  if (next.area) query.area = next.area
  if (next.priceMin !== undefined) query.price_min = String(next.priceMin)
  if (next.priceMax !== undefined) query.price_max = String(next.priceMax)
  router.replace({ path: '/rank', query })
  reload()
}

function openDetail(item: RankItem) {
  router.push({ path: `/detail/${item.restaurant_id}` })
}

function onShare() {
  shareLink({
    title: `${city.value}苍蝇馆子榜`,
    text: '本地人才知道的宝藏小店',
    url: window.location.href,
  })
}
</script>

<style scoped>
.rank {
  padding-top: 16px;
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
</style>