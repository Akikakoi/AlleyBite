<template>
  <div class="page mine">
    <header class="mine__bar">
      <button class="mine__back" aria-label="返回" @click="router.back()">‹</button>
      <span class="text-sub">我的</span>
    </header>

    <template v-if="store.isLoggedIn">
      <section class="card mine__user">
        <div class="mine__avatar">{{ avatarChar }}</div>
        <div class="mine__user-info">
          <p class="mine__username">{{ store.username }}</p>
          <p class="text-sub">收藏 {{ favoriteCount }} 家店</p>
        </div>
      </section>

      <section class="card mine__entry" @click="router.push('/mine/favorites')">
        <span class="mine__entry-icon">★</span>
        <span class="mine__entry-label">我的收藏</span>
        <span class="mine__entry-arrow">›</span>
      </section>

      <van-button
        class="mine__logout"
        round
        plain
        block
        @click="onLogout"
      >
        退出登录
      </van-button>
    </template>

    <EmptyState
      v-else
      title="还未登录"
      desc="登录后可以收藏店铺，随时回来翻榜单"
    >
      <template #action>
        <van-button type="primary" round @click="toLogin">去登录</van-button>
      </template>
    </EmptyState>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { showConfirmDialog, showToast } from 'vant'

import { listFavorites } from '@/api/favorites'
import { ApiError } from '@/api/request'
import EmptyState from '@/components/EmptyState.vue'
import { useUserStore } from '@/store/user'

const router = useRouter()
const store = useUserStore()

const favoriteCount = ref(0)

const avatarChar = computed(() => (store.username || '?').slice(0, 1).toUpperCase())

async function loadCount() {
  try {
    favoriteCount.value = (await listFavorites()).length
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) {
      store.logout()
    }
    // 其余失败静默：计数仅作展示
  }
}

function toLogin() {
  router.push({ path: '/login', query: { redirect: '/mine' } })
}

async function onLogout() {
  try {
    await showConfirmDialog({ title: '退出登录', message: '确定要退出当前账号吗？' })
  } catch {
    return
  }
  store.logout()
  showToast('已退出')
}

onMounted(() => {
  if (store.isLoggedIn) loadCount()
})
</script>

<style scoped>
.mine {
  padding-top: 16px;
}

.mine__bar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}

.mine__back {
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

.mine__user {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 18px 16px;
}

.mine__avatar {
  width: 48px;
  height: 48px;
  border-radius: 50%;
  background: var(--color-primary-soft);
  color: var(--color-primary);
  font-size: 22px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
}

.mine__username {
  margin: 0;
  font-size: var(--font-title);
  font-weight: 700;
}

.mine__user-info p {
  margin: 2px 0 0;
}

.mine__entry {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 12px;
  padding: 16px;
  cursor: pointer;
}

.mine__entry-icon {
  color: var(--color-primary);
  font-size: 18px;
}

.mine__entry-label {
  flex: 1;
  font-weight: 600;
}

.mine__entry-arrow {
  color: var(--color-text-sub);
  font-size: 18px;
}

.mine__logout {
  margin-top: 20px;
}
</style>
