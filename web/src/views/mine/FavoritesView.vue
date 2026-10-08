<template>
  <div class="page fav">
    <header class="fav__bar">
      <button class="fav__back" aria-label="返回" @click="router.back()">‹</button>
      <div class="fav__title">
        <h1 class="page-title">我的收藏</h1>
        <p class="text-sub">共 {{ items.length }} 家店</p>
      </div>
    </header>

    <EmptyState v-if="state === 'loading'" title="加载中…" />
    <EmptyState v-else-if="state === 'error'" title="收藏加载失败" :desc="errorMessage">
      <template #action>
        <van-button type="primary" round @click="load">重新加载</van-button>
      </template>
    </EmptyState>

    <template v-else>
      <div v-if="items.length" class="list-grid">
        <article
          v-for="item in items"
          :key="item.restaurant_id"
          class="card fav-card"
          @click="openDetail(item)"
        >
          <div class="fav-card__body">
            <div class="fav-card__head">
              <h3 class="fav-card__name">{{ item.name }}</h3>
              <span class="text-sub">{{ formatPrice(item.avg_price) }}</span>
            </div>
            <p class="text-sub fav-card__meta">
              {{ [item.city, item.area, item.cuisine].filter(Boolean).join(' · ') || '区域待补充' }}
            </p>
            <p v-if="item.address" class="text-sub fav-card__addr">{{ item.address }}</p>
            <p class="text-sub fav-card__foot">
              {{ item.mention_count }} 条口碑
              <template v-if="item.favorited_at">
                · 收藏于 {{ formatDate(item.favorited_at) }}
              </template>
            </p>
          </div>
          <van-button
            class="fav-card__remove"
            size="small"
            round
            plain
            type="danger"
            @click.stop="onRemove(item)"
          >
            取消收藏
          </van-button>
        </article>
      </div>
      <EmptyState
        v-else
        title="还没有收藏"
        desc="逛榜单时点「收藏」，好吃的就不会错过"
      >
        <template #action>
          <van-button type="primary" round @click="router.push('/')">去逛榜单</van-button>
        </template>
      </EmptyState>
    </template>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { showToast } from 'vant'

import { listFavorites, removeFavorite, type FavoriteItem } from '@/api/favorites'
import { ApiError } from '@/api/request'
import EmptyState from '@/components/EmptyState.vue'
import { formatDate, formatPrice } from '@/utils/format'

const router = useRouter()

const items = ref<FavoriteItem[]>([])
const state = ref<'loading' | 'ready' | 'error'>('loading')
const errorMessage = ref('')

async function load() {
  state.value = 'loading'
  try {
    items.value = await listFavorites()
    state.value = 'ready'
  } catch (err) {
    state.value = 'error'
    errorMessage.value = err instanceof ApiError ? err.message : '加载失败，请稍后重试'
  }
}

function openDetail(item: FavoriteItem) {
  router.push({ path: `/detail/${item.restaurant_id}` })
}

async function onRemove(item: FavoriteItem) {
  try {
    await removeFavorite(item.restaurant_id)
    items.value = items.value.filter((i) => i.restaurant_id !== item.restaurant_id)
    showToast('已取消收藏')
  } catch (err) {
    showToast(err instanceof ApiError ? err.message : '操作失败，请稍后重试')
  }
}

onMounted(load)
</script>

<style scoped>
.fav {
  padding-top: 16px;
}

.fav__bar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}

.fav__back {
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

.fav__title .page-title {
  font-size: 20px;
}

.fav__title .text-sub {
  margin: 2px 0 0;
}

.fav-card {
  display: flex;
  gap: 12px;
  padding: 16px;
  cursor: pointer;
  transition: transform 0.15s ease, box-shadow 0.15s ease;
}

.fav-card:active {
  transform: scale(0.99);
}

.fav-card__body {
  flex: 1;
  min-width: 0;
}

.fav-card__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}

.fav-card__name {
  margin: 0;
  font-size: var(--font-title);
  font-weight: 700;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.fav-card__meta,
.fav-card__addr,
.fav-card__foot {
  margin: 2px 0 0;
}

.fav-card__addr {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.fav-card__foot {
  margin-top: 6px;
}

.fav-card__remove {
  flex: none;
  align-self: center;
}
</style>
