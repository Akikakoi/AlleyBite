<template>
  <div class="page detail">
    <header class="detail__bar">
      <button class="detail__back" aria-label="返回" @click="router.back()">‹</button>
      <span class="text-sub">店铺详情</span>
      <span class="detail__bar-spacer" />
    </header>

    <EmptyState v-if="state === 'loading'" title="加载中…" />
    <EmptyState v-else-if="state === 'error'" title="店铺加载失败" :desc="errorMessage">
      <template #action>
        <van-button type="primary" round @click="load">重新加载</van-button>
      </template>
    </EmptyState>
    <EmptyState
      v-else-if="state === 'notfound'"
      title="没有找到这家店"
      desc="它可能已被合并或下架"
    >
      <template #action>
        <van-button type="primary" round @click="router.push('/')">回首页</van-button>
      </template>
    </EmptyState>

    <template v-else-if="detail">
      <section class="card detail__head">
        <div>
          <h1 class="page-title">{{ detail.name }}</h1>
          <p class="text-sub detail__sub">
            {{ [detail.area, detail.cuisine].filter(Boolean).join(' · ') || '区域待补充' }}
            · {{ formatPrice(detail.avg_price) }}
          </p>
        </div>
        <div class="detail__score">
          <span class="detail__score-num">{{ formatScore(detail.score) }}</span>
          <span class="text-sub">综合分</span>
        </div>
      </section>

      <section v-if="detail.recommended_dishes.length" class="card detail__block">
        <h2 class="detail__label">推荐菜</h2>
        <div class="detail__tags">
          <KeywordTag v-for="dish in detail.recommended_dishes" :key="dish" :text="dish" />
        </div>
      </section>

      <section
        v-if="detail.praise_keywords.length || detail.complaints.length"
        class="card detail__block"
      >
        <h2 class="detail__label">口碑关键词</h2>
        <div class="detail__tags">
          <KeywordTag
            v-for="kw in detail.praise_keywords"
            :key="`good-${kw}`"
            :text="kw"
            type="good"
          />
          <KeywordTag
            v-for="kw in detail.complaints"
            :key="`warn-${kw}`"
            :text="kw"
            type="warn"
          />
        </div>
      </section>

      <section class="card detail__block">
        <h2 class="detail__label">地址</h2>
        <p class="detail__address">{{ detail.address || '地址待补充' }}</p>
        <van-button
          v-if="detail.address"
          size="small"
          round
          type="primary"
          plain
          @click="navigate"
        >
          一键导航
        </van-button>
      </section>

      <section v-if="detail.sources.length" class="card detail__block">
        <h2 class="detail__label">来源引用</h2>
        <ul class="detail__sources">
          <li
            v-for="(source, index) in detail.sources"
            :key="index"
            class="detail__source"
            @click="openSource(source)"
          >
            <p class="detail__source-head">
              {{ sourceLabel(source.source) }}
              <template v-if="source.title">· {{ source.title }}</template>
            </p>
            <p v-if="source.excerpt" class="text-sub detail__source-excerpt">
              {{ source.excerpt }}
            </p>
          </li>
        </ul>
        <p class="text-sub">点击来源将跳转第三方页面，内容由其负责</p>
      </section>

      <p class="text-sub detail__disclaimer">
        数据来源于公开信息，仅供参考，信息可能滞后。
      </p>

      <div class="detail__actions">
        <van-button round plain type="primary" @click="onShare">分享这家店</van-button>
        <van-button round plain @click="onFeedback">纠错</van-button>
      </div>

      <van-popup
        v-model:show="showFeedback"
        round
        position="bottom"
        :style="{ padding: '20px 16px 24px' }"
      >
        <h2 class="feedback__title">纠错 / 反馈</h2>
        <p class="text-sub feedback__hint">{{ detail.name }}</p>

        <div class="feedback__types">
          <button
            v-for="option in FEEDBACK_TYPES"
            :key="option.value"
            type="button"
            class="feedback__type"
            :class="{ 'feedback__type--on': form.type === option.value }"
            @click="form.type = option.value"
          >
            {{ option.label }}
          </button>
        </div>

        <van-field
          v-model="form.content"
          type="textarea"
          rows="3"
          maxlength="500"
          show-word-limit
          autosize
          placeholder="请描述哪里有问题，例如地址、人均、标签不准确"
        />
        <van-field
          v-model="form.contact"
          label="联系方式"
          maxlength="128"
          placeholder="选填，便于我们回访"
        />

        <van-button
          class="feedback__submit"
          type="primary"
          round
          block
          :loading="submitting"
          @click="submitFeedbackForm"
        >
          提交
        </van-button>
      </van-popup>
    </template>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { showToast } from 'vant'

import { getRestaurant } from '@/api/restaurants'
import { submitFeedback } from '@/api/feedback'
import { ApiError, isNotFound } from '@/api/request'
import EmptyState from '@/components/EmptyState.vue'
import KeywordTag from '@/components/KeywordTag.vue'
import type { FeedbackType, RestaurantDetail, SourceRef } from '@/types'
import { formatPrice, formatScore, sourceLabel } from '@/utils/format'
import { shareLink } from '@/utils/share'

const FEEDBACK_TYPES: { value: FeedbackType; label: string }[] = [
  { value: 'info', label: '信息有误' },
  { value: 'closed', label: '已关停' },
  { value: 'label', label: '标签不当' },
  { value: 'other', label: '其他' },
]

const route = useRoute()
const router = useRouter()

const detail = ref<RestaurantDetail | null>(null)
const state = ref<'loading' | 'ready' | 'error' | 'notfound'>('loading')
const errorMessage = ref('')

const showFeedback = ref(false)
const submitting = ref(false)
const form = ref<{ type: FeedbackType; content: string; contact: string }>({
  type: 'info',
  content: '',
  contact: '',
})

async function load() {
  state.value = 'loading'
  try {
    detail.value = await getRestaurant(String(route.params.id))
    state.value = 'ready'
  } catch (err) {
    if (isNotFound(err)) {
      state.value = 'notfound'
    } else {
      state.value = 'error'
      errorMessage.value = err instanceof ApiError ? err.message : '加载失败，请稍后重试'
    }
  }
}

function navigate() {
  if (!detail.value?.address) return
  window.open(
    `https://uri.amap.com/search?keyword=${encodeURIComponent(detail.value.address)}`,
    '_blank',
    'noopener',
  )
}

function openSource(source: SourceRef) {
  router.push({
    path: '/source',
    query: {
      url: source.source_url ?? '',
      source: source.source,
      title: source.title ?? '',
    },
  })
}

function onShare() {
  if (!detail.value) return
  shareLink({
    title: detail.value.name,
    text: `¥${detail.value.avg_price ?? '待补充'} · ${detail.value.area ?? ''}`,
    url: window.location.href,
  })
}

function onFeedback() {
  form.value = { type: 'info', content: '', contact: '' }
  showFeedback.value = true
}

async function submitFeedbackForm() {
  const content = form.value.content.trim()
  if (!content) {
    showToast('请先描述问题')
    return
  }
  submitting.value = true
  try {
    await submitFeedback({
      restaurant_id: detail.value?.restaurant_id,
      type: form.value.type,
      content,
      contact: form.value.contact.trim() || undefined,
    })
    showFeedback.value = false
    showToast('已收到反馈，感谢纠正')
  } catch (err) {
    showToast(err instanceof ApiError ? err.message : '提交失败，请稍后重试')
  } finally {
    submitting.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.detail {
  padding-top: 16px;
}

.detail__bar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}

.detail__back {
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

.detail__bar-spacer {
  flex: 1;
}

.detail__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 18px 16px;
}

.detail__sub {
  margin: 4px 0 0;
}

.detail__score {
  flex: none;
  display: flex;
  flex-direction: column;
  align-items: center;
}

.detail__score-num {
  font-size: 26px;
  font-weight: 800;
  color: var(--color-primary);
  line-height: 1.2;
}

.detail__block {
  margin-top: 12px;
  padding: 16px;
}

.detail__label {
  margin: 0 0 10px;
  font-size: var(--font-body);
  font-weight: 700;
  color: var(--color-secondary);
}

.detail__tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.detail__address {
  margin: 0 0 12px;
}

.detail__sources {
  list-style: none;
  margin: 0 0 8px;
  padding: 0;
}

.detail__source {
  padding: 10px 0;
  border-bottom: 1px solid var(--color-border);
  cursor: pointer;
}

.detail__source:last-child {
  border-bottom: none;
}

.detail__source-head {
  margin: 0;
  font-weight: 600;
}

.detail__source-excerpt {
  margin: 4px 0 0;
}

.detail__disclaimer {
  margin: 16px 0 0;
  text-align: center;
}

.detail__actions {
  display: flex;
  gap: 12px;
  margin-top: 16px;
}

.detail__actions .van-button {
  flex: 1;
}

.feedback__title {
  margin: 0;
  font-size: 17px;
  font-weight: 700;
  color: var(--color-secondary);
}

.feedback__hint {
  margin: 4px 0 14px;
}

.feedback__types {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 12px;
}

.feedback__type {
  padding: 6px 14px;
  border: 1px solid var(--color-border);
  border-radius: 999px;
  background: var(--color-surface);
  color: var(--color-secondary);
  font-size: 13px;
  cursor: pointer;
}

.feedback__type--on {
  border-color: var(--color-primary);
  color: var(--color-primary);
  font-weight: 600;
}

.feedback__submit {
  margin-top: 16px;
}
</style>