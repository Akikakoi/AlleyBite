<template>
  <div class="page trips">
    <header class="trips__bar">
      <button class="trips__back press-feedback" aria-label="返回" @click="router.back()">‹</button>
      <div class="trips__title">
        <h1 class="page-title">行前清单</h1>
        <p class="text-sub">勾了 {{ items.length }} 家店</p>
      </div>
    </header>

    <LoadingState v-if="state === 'loading'" />
    <EmptyState v-else-if="state === 'error'" title="清单加载失败" :desc="errorMessage">
      <template #action>
        <van-button type="primary" round @click="load">重新加载</van-button>
      </template>
    </EmptyState>

    <template v-else>
      <div v-if="groups.length" class="trips__list">
        <section v-for="group in groups" :key="group.city" class="trips__city">
          <h2 class="trips__city-name">{{ group.city }}</h2>
          <article
            v-for="item in group.items"
            :key="item.restaurant_id"
            class="card trip-card press-feedback"
            @click="openDetail(item)"
          >
            <div class="trip-card__head">
              <h3 class="trip-card__name">{{ item.name }}</h3>
              <span
                class="trip-card__status"
                :class="{ 'trip-card__status--visited': item.status === 'visited' }"
              >
                {{ item.status === 'visited' ? '已去' : '待去' }}
              </span>
            </div>
            <p class="text-sub trip-card__meta">{{ subLine(item) }}</p>
            <p v-if="item.address" class="text-sub trip-card__meta">{{ item.address }}</p>
            <p class="text-sub trip-card__foot">
              勾于 {{ formatDate(item.saved_at) }}
              <template v-if="item.visited_at"> · 核销于 {{ formatDate(item.visited_at) }}</template>
            </p>
          </article>
        </section>
        <p class="text-sub trips__hint">
          勾店与核销在小程序端：榜单卡片上勾店，路上核销并打卡；本页仅供查看。
        </p>
      </div>
      <EmptyState
        v-else
        title="清单还是空的"
        desc="勾店入口在小程序端：榜单点「勾店」收进来，本页随时查看进度"
      >
        <template #action>
          <van-button type="primary" round @click="router.push('/')">去逛榜单</van-button>
        </template>
      </EmptyState>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { listTripItems, type TripItem } from '@/api/tripItems'
import { ApiError } from '@/api/request'
import EmptyState from '@/components/EmptyState.vue'
import LoadingState from '@/components/LoadingState.vue'
import { formatDate, formatPrice } from '@/utils/format'
import { useUserStore } from '@/store/user'

const router = useRouter()
const route = useRoute()
const userStore = useUserStore()

const items = ref<TripItem[]>([])
const state = ref<'loading' | 'ready' | 'error'>('loading')
const errorMessage = ref('')

/** 按城聚合（设计方案 2.4：一城一清单，不建清单头），保持勾店时间倒序 */
const groups = computed(() => {
  const byCity = new Map<string, TripItem[]>()
  for (const item of items.value) {
    const city = item.city || '其他城市'
    const list = byCity.get(city)
    if (list) list.push(item)
    else byCity.set(city, [item])
  }
  return Array.from(byCity, ([city, list]) => ({ city, items: list }))
})

function subLine(item: TripItem) {
  return [item.area, item.cuisine, formatPrice(item.avg_price)]
    .filter(Boolean)
    .join(' · ')
}

async function load() {
  state.value = 'loading'
  try {
    items.value = await listTripItems()
    state.value = 'ready'
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) {
      // 令牌过期/无效：清态回登录页，而不是整页报错
      userStore.logout()
      router.replace({ path: '/login', query: { redirect: route.fullPath } })
      return
    }
    state.value = 'error'
    errorMessage.value = err instanceof ApiError ? err.message : '加载失败，请稍后重试'
  }
}

function openDetail(item: TripItem) {
  router.push(`/detail/${item.restaurant_id}`)
}

onMounted(load)
</script>

<style scoped>
.trips {
  padding-top: 16px;
}

.trips__bar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}

.trips__back {
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

.trips__title .page-title {
  font-size: 20px;
}

.trips__title .text-sub {
  margin: 2px 0 0;
}

.trips__city {
  margin-bottom: 16px;
}

.trips__city-name {
  margin: 0 4px 8px;
  font-size: 15px;
  font-weight: 700;
}

.trip-card {
  margin-bottom: 10px;
  padding: 14px 16px;
  cursor: pointer;
}

.trip-card__head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.trip-card__name {
  flex: 1;
  min-width: 0;
  margin: 0;
  font-size: var(--font-title);
  font-weight: 700;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 状态徽标：待去琥珀 / 已去绿 */
.trip-card__status {
  flex: none;
  padding: 2px 8px;
  border-radius: var(--radius-tag);
  font-size: 11px;
  background: var(--color-warn-soft);
  color: var(--color-warn);
}

.trip-card__status--visited {
  background: var(--color-good-soft);
  color: var(--color-good);
}

.trip-card__meta,
.trip-card__foot {
  margin: 4px 0 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.trips__hint {
  margin: 12px 4px 0;
  font-size: 11px;
  line-height: 1.6;
  opacity: 0.7;
}
</style>
