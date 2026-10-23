<template>
  <div class="page packs">
    <header class="packs__bar">
      <button class="packs__back press-feedback" aria-label="返回" @click="router.back()">‹</button>
      <div class="packs__title">
        <h1 class="page-title">我的收藏包</h1>
        <p class="text-sub">已收藏 {{ items.length }} 城</p>
      </div>
    </header>

    <LoadingState v-if="state === 'loading'" />
    <EmptyState v-else-if="state === 'error'" title="收藏包加载失败" :desc="errorMessage">
      <template #action>
        <van-button type="primary" round @click="load">重新加载</van-button>
      </template>
    </EmptyState>

    <template v-else>
      <div v-if="items.length" class="packs__list">
        <article
          v-for="item in items"
          :key="item.city"
          class="card pack-card press-feedback"
          @click="openPack(item)"
        >
          <div class="pack-card__body">
            <div class="pack-card__head">
              <h3 class="pack-card__city">{{ item.city }}苍蝇馆子榜</h3>
              <span class="pack-card__maturity" :class="`pack-card__maturity--${item.maturity}`">
                {{ MATURITY_LABELS[item.maturity] ?? '少量收录' }}
              </span>
            </div>
            <p class="text-sub pack-card__meta">
              {{ item.rank_count }} 家上榜
              <template v-if="item.generated_at">
                · 更新 {{ formatDate(item.generated_at) }}
              </template>
            </p>
            <p class="text-sub pack-card__foot">收藏于 {{ formatDate(item.packed_at) }}</p>
          </div>
          <van-button
            class="pack-card__remove"
            size="small"
            round
            plain
            type="danger"
            @click.stop="onRemove(item)"
          >
            退订
          </van-button>
        </article>
        <p class="text-sub packs__hint">
          打开收藏包总是拉取最新榜单；断网时自动使用本机缓存并标注数据截至时间。
        </p>
      </div>
      <EmptyState
        v-else
        title="还没有收藏包"
        desc="逛榜单时点「收藏整城」，出行前整榜带走"
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
import { useRoute, useRouter } from 'vue-router'
import { showConfirmDialog, showToast } from 'vant'

import { listCityPacks, unsubscribeCityPack, type CityPackItem } from '@/api/cityPacks'
import { ApiError } from '@/api/request'
import EmptyState from '@/components/EmptyState.vue'
import LoadingState from '@/components/LoadingState.vue'
import { formatDate } from '@/utils/format'
import { useUserStore } from '@/store/user'

const router = useRouter()
const route = useRoute()
const userStore = useUserStore()

const MATURITY_LABELS: Record<string, string> = {
  deep: '深度覆盖',
  initial: '初步覆盖',
  collecting: '少量收录',
}

const items = ref<CityPackItem[]>([])
const state = ref<'loading' | 'ready' | 'error'>('loading')
const errorMessage = ref('')

async function load() {
  state.value = 'loading'
  try {
    items.value = await listCityPacks()
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

/** 打开收藏包 = 跳榜单页实时拉最新快照；离线副本由榜单页兜底 */
function openPack(item: CityPackItem) {
  router.push({ path: '/rank', query: { city: item.city } })
}

async function onRemove(item: CityPackItem) {
  try {
    await showConfirmDialog({
      title: '退订收藏包',
      message: `不再收藏「${item.city}」榜单？`,
    })
  } catch {
    return
  }
  try {
    await unsubscribeCityPack(item.city)
    items.value = items.value.filter((i) => i.city !== item.city)
    showToast('已退订')
  } catch (err) {
    showToast(err instanceof ApiError ? err.message : '操作失败，请稍后重试')
  }
}

onMounted(load)
</script>

<style scoped>
.packs {
  padding-top: 16px;
}

.packs__bar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}

.packs__back {
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

.packs__title .page-title {
  font-size: 20px;
}

.packs__title .text-sub {
  margin: 2px 0 0;
}

.pack-card {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
  padding: 16px;
  cursor: pointer;
}

.pack-card__body {
  flex: 1;
  min-width: 0;
}

.pack-card__head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.pack-card__city {
  margin: 0;
  font-size: var(--font-title);
  font-weight: 700;
}

.pack-card__maturity {
  flex: none;
  padding: 2px 8px;
  border-radius: var(--radius-tag);
  font-size: 11px;
  background: var(--color-good-soft);
  color: var(--color-good);
}

.pack-card__maturity--initial {
  background: var(--color-warn-soft);
  color: var(--color-warn);
}

.pack-card__maturity--collecting {
  background: var(--color-primary-soft);
  color: var(--color-primary);
}

.pack-card__meta,
.pack-card__foot {
  margin: 4px 0 0;
}

.packs__hint {
  margin: 12px 4px 0;
  font-size: 11px;
  line-height: 1.6;
  opacity: 0.7;
}
</style>
