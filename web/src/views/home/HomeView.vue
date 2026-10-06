<template>
  <div class="page home">
    <header class="home__hero">
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
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { getCities } from '@/api/cities'
import { ApiError } from '@/api/request'
import EmptyState from '@/components/EmptyState.vue'
import { useCityStore } from '@/store/city'
import type { City } from '@/types'

const router = useRouter()
const store = useCityStore()

const keyword = ref('')
const cities = ref<City[]>([])
const error = ref('')

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

onMounted(load)
</script>

<style scoped>
.home__hero {
  padding: 32px 0 8px;
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