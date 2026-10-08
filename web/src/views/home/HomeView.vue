<template>
  <div class="page home">
    <header class="home__hero">
      <div class="home__topbar">
        <button class="home__mine" @click="router.push('/mine')">
          {{ userStore.isLoggedIn ? `我的 · ${userStore.username}` : '我的' }}
        </button>
      </div>
      <p class="home__eyebrow">HOLE-IN-THE-WALL</p>
      <h1 class="page-title">钻进巷子，找本地真味</h1>
      <p class="text-sub home__sub">输入城市，看看被反复念叨的小馆子</p>
      <van-search
        v-model="keyword"
        shape="round"
        background="transparent"
        placeholder="输入城市名，如：成都"
        show-action
        @search="go(keyword)"
      >
        <template #action>
          <div class="home__search-btn" @click="go(keyword)">搜索</div>
        </template>
      </van-search>
    </header>

    <section v-if="recommend.items.length" class="home__section">
      <h2 class="home__label">
        猜你想吃
        <span class="text-sub home__label-sub">
          {{ recommend.strategy === 'taste' ? '按你的口味' : '本站热门' }}
        </span>
      </h2>
      <div class="home__recs">
        <article
          v-for="item in recommend.items"
          :key="item.restaurant_id"
          class="card home__rec"
          @click="goDetail(item)"
        >
          <p class="home__rec-name">{{ item.name }}</p>
          <p class="text-sub">
            {{ [item.city, item.cuisine].filter(Boolean).join(' · ') || '等你探店' }}
          </p>
        </article>
      </div>
    </section>

    <section class="home__section">
      <h2 class="home__label">热门城市</h2>
      <EmptyState
        v-if="error"
        title="城市列表加载失败"
        :desc="error"
      >
        <template #action>
          <van-button type="primary" round @click="load">重新加载</van-button>
        </template>
      </EmptyState>
      <div v-else class="city-grid">
        <button
          v-for="city in cities"
          :key="city.name"
          class="city-chip card"
          @click="go(city.name)"
        >
          <span class="city-chip__name">{{ city.name }}</span>
          <span class="city-chip__status text-sub">
            {{ city.has_rank ? '有榜单' : '正在收录' }}
          </span>
        </button>
      </div>
    </section>

    <section v-if="store.recent.length" class="home__section">
      <h2 class="home__label">最近浏览</h2>
      <div class="city-grid">
        <button
          v-for="name in store.recent"
          :key="name"
          class="city-chip card"
          @click="go(name)"
        >
          <span class="city-chip__name">{{ name }}</span>
        </button>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'

import { getCities } from '@/api/cities'
import { ApiError } from '@/api/request'
import { getRecommend, type RecommendData } from '@/api/recommend'
import EmptyState from '@/components/EmptyState.vue'
import { useCityStore } from '@/store/city'
import { useUserStore } from '@/store/user'
import type { City } from '@/types'

const router = useRouter()
const store = useCityStore()
const userStore = useUserStore()

const keyword = ref('')
const cities = ref<City[]>([])
const error = ref('')
const recommend = reactive<RecommendData>({ strategy: 'hot', items: [] })

async function loadRecommend() {
  try {
    const data = await getRecommend(4)
    recommend.strategy = data.strategy
    recommend.items = data.items
  } catch {
    // 推荐失败静默：非核心路径
  }
}

function goDetail(item: { restaurant_id: number }) {
  router.push({ path: `/detail/${item.restaurant_id}` })
}

async function load() {
  error.value = ''
  try {
    cities.value = await getCities()
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : '加载失败，请稍后重试'
  }
}

function go(name: string) {
  const value = (name || '').trim()
  if (!value) return
  store.setCity(value)
  router.push({ path: '/rank', query: { city: value } })
}

onMounted(() => {
  load()
  loadRecommend()
})
</script>

<style scoped>
.home__hero {
  padding: 32px 0 8px;
}

.home__topbar {
  display: flex;
  justify-content: flex-end;
  margin-bottom: 8px;
}

.home__mine {
  padding: 6px 14px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-tag);
  background: var(--color-surface);
  color: var(--color-secondary);
  font-size: var(--font-hint);
  cursor: pointer;
}

.home__eyebrow {
  margin: 0;
  font-size: var(--font-hint);
  letter-spacing: 2px;
  color: var(--color-primary);
  font-weight: 700;
}

.home__sub {
  margin: 4px 0 16px;
}

.home__search-btn {
  color: var(--color-primary);
  font-weight: 600;
  padding: 0 4px;
}

.home__section {
  margin-top: 28px;
}

.home__label-sub {
  margin-left: 8px;
  font-weight: 400;
}

.home__recs {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

@media (min-width: 768px) {
  .home__recs {
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }
}

.home__rec {
  padding: 12px 14px;
  cursor: pointer;
}

.home__rec-name {
  margin: 0;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.home__rec p + p {
  margin: 2px 0 0;
}

.home__label {
  margin: 0 0 12px;
  font-size: var(--font-body);
  font-weight: 700;
  color: var(--color-secondary);
}

.city-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

@media (min-width: 768px) {
  .city-grid {
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }
}

.city-chip {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  padding: 14px 16px;
  border: 1px solid var(--color-border);
  font-size: var(--font-body);
  color: var(--color-text);
  cursor: pointer;
  text-align: left;
}

.city-chip__name {
  font-weight: 600;
}

.city-chip__status {
  font-size: var(--font-hint);
}
</style>