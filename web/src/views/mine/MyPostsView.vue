<template>
  <div class="page myposts">
    <header class="myposts__bar">
      <button class="myposts__back" aria-label="返回" @click="router.back()">‹</button>
      <div class="myposts__title">
        <h1 class="page-title">我的打卡</h1>
        <p class="text-sub">发布后经审核展示</p>
      </div>
    </header>

    <EmptyState v-if="state === 'loading'" title="加载中…" />
    <EmptyState v-else-if="state === 'error'" title="加载失败" :desc="errorMessage">
      <template #action>
        <van-button type="primary" round @click="load">重新加载</van-button>
      </template>
    </EmptyState>

    <template v-else>
      <div v-if="items.length" class="myposts__list">
        <article v-for="item in items" :key="item.id" class="card myposts__item">
          <p class="myposts__head">
            <span
              class="myposts__status"
              :class="`myposts__status--${item.status}`"
            >
              {{ statusLabel(item.status) }}
            </span>
            <span class="text-sub">{{ formatDate(item.created_at) }}</span>
          </p>
          <p class="myposts__shop" @click="openDetail(item)">
            {{ item.restaurant_name || '店铺详情' }} ›
          </p>
          <p class="myposts__content">{{ item.content }}</p>
          <div v-if="item.images.length" class="myposts__imgs">
            <img
              v-for="(img, idx) in item.images"
              :key="idx"
              :src="img"
              class="myposts__img"
              alt="打卡图片"
              loading="lazy"
            />
          </div>
        </article>
      </div>
      <EmptyState v-else title="还没有打卡" desc="逛详情页时点「打卡」，记下这口好味道">
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

import { listMyUgc, type UgcItem } from '@/api/ugc'
import { ApiError } from '@/api/request'
import EmptyState from '@/components/EmptyState.vue'
import { formatDate } from '@/utils/format'

const router = useRouter()

const items = ref<UgcItem[]>([])
const state = ref<'loading' | 'ready' | 'error'>('loading')
const errorMessage = ref('')

function statusLabel(status: string) {
  if (status === 'approved') return '已展示'
  if (status === 'rejected') return '未通过'
  return '审核中'
}

async function load() {
  state.value = 'loading'
  try {
    items.value = await listMyUgc()
    state.value = 'ready'
  } catch (err) {
    state.value = 'error'
    errorMessage.value = err instanceof ApiError ? err.message : '加载失败，请稍后重试'
  }
}

function openDetail(item: UgcItem) {
  router.push({ path: `/detail/${item.restaurant_id}` })
}

onMounted(load)
</script>

<style scoped>
.myposts {
  padding-top: 16px;
}

.myposts__bar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}

.myposts__back {
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

.myposts__title .page-title {
  font-size: 20px;
}

.myposts__title .text-sub {
  margin: 2px 0 0;
}

.myposts__list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.myposts__item {
  padding: 14px 16px;
}

.myposts__head {
  margin: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.myposts__status {
  font-size: var(--font-hint);
  padding: 2px 10px;
  border-radius: var(--radius-tag);
}

.myposts__status--approved {
  color: var(--color-good);
  background: var(--color-good-soft);
}

.myposts__status--pending {
  color: var(--color-warn);
  background: var(--color-warn-soft);
}

.myposts__status--rejected {
  color: var(--color-text-sub);
  background: rgba(122, 115, 108, 0.12);
}

.myposts__shop {
  margin: 8px 0 0;
  color: var(--color-primary);
  font-weight: 600;
  cursor: pointer;
}

.myposts__content {
  margin: 4px 0 0;
}

.myposts__imgs {
  display: flex;
  gap: 8px;
  margin-top: 8px;
  overflow-x: auto;
}

.myposts__img {
  width: 88px;
  height: 88px;
  object-fit: cover;
  border-radius: 8px;
  flex: none;
}
</style>
