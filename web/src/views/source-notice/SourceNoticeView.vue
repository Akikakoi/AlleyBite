<template>
  <div class="page source">
    <header class="source__bar">
      <button class="source__back" aria-label="返回" @click="router.back()">‹</button>
      <span class="text-sub">来源提示</span>
      <span class="source__bar-spacer" />
    </header>

    <section class="card source__card">
      <h1 class="page-title">即将离开本站</h1>
      <p class="text-sub source__desc">
        你将前往第三方来源「{{ sourceLabel(sourceName) }}」查看原文。该页面内容由第三方提供并负责，
        本站仅作公开信息聚合与溯源引用，不对其内容的准确性与时效性作出保证。
      </p>
      <p v-if="title" class="source__title">{{ title }}</p>
      <div class="source__actions">
        <van-button round type="primary" :disabled="!url" @click="go">
          继续访问
        </van-button>
        <van-button round plain @click="router.back()">返回</van-button>
      </div>
      <p v-if="!url" class="text-sub source__hint">该来源未提供可跳转的原文链接</p>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { sourceLabel } from '@/utils/format'

const route = useRoute()
const router = useRouter()

const url = computed(() => String(route.query.url || ''))
const sourceName = computed(() =>
  route.query.source ? String(route.query.source) : '',
)
const title = computed(() => (route.query.title ? String(route.query.title) : ''))

function go() {
  if (!url.value) return
  window.open(url.value, '_blank', 'noopener')
}
</script>

<style scoped>
.source {
  padding-top: 16px;
}

.source__bar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}

.source__back {
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

.source__bar-spacer {
  flex: 1;
}

.source__card {
  padding: 24px 20px;
}

.source__desc {
  margin: 12px 0 0;
}

.source__title {
  margin: 16px 0 0;
  padding: 12px 14px;
  border-radius: 12px;
  background: var(--color-primary-soft);
  font-weight: 600;
}

.source__actions {
  display: flex;
  gap: 12px;
  margin-top: 24px;
}

.source__actions .van-button {
  flex: 1;
}

.source__hint {
  margin: 12px 0 0;
  text-align: center;
}
</style>