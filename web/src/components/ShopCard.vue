<template>
  <article class="shop-card card" @click="emit('click')">
    <div class="shop-card__rank" :class="{ 'is-top': item.rank <= 3 }">
      {{ item.rank }}
    </div>

    <div class="shop-card__body">
      <div class="shop-card__head">
        <h3 class="shop-card__name">{{ item.name }}</h3>
        <span class="shop-card__score">{{ formatScore(item.score) }}</span>
      </div>

      <p class="text-sub shop-card__meta">
        {{ metaText }}
        <span class="shop-card__dot">·</span>
        {{ formatPrice(item.avg_price) }}
      </p>

      <div v-if="item.praise_keywords.length" class="shop-card__tags">
        <KeywordTag
          v-for="kw in item.praise_keywords.slice(0, 4)"
          :key="kw"
          :text="kw"
          type="good"
        />
      </div>

      <p v-if="item.recommended_dishes.length" class="text-sub shop-card__dishes">
        推荐：{{ item.recommended_dishes.slice(0, 3).join('、') }}
      </p>

      <p class="text-sub shop-card__foot">
        {{ item.mention_count }} 条口碑
        <template v-if="item.last_mentioned_at">
          · 最近 {{ formatDate(item.last_mentioned_at) }}
        </template>
      </p>
    </div>
  </article>
</template>

<script setup lang="ts">
import { computed } from 'vue'

import KeywordTag from './KeywordTag.vue'

import type { RankItem } from '@/types'
import { formatDate, formatPrice, formatScore } from '@/utils/format'

const props = defineProps<{ item: RankItem }>()

const emit = defineEmits<{ (e: 'click'): void }>()

const metaText = computed(
  () => [props.item.area, props.item.cuisine].filter(Boolean).join(' · ') || '区域待补充',
)
</script>

<style scoped>
.shop-card {
  position: relative;
  display: flex;
  gap: 12px;
  padding: 16px;
  cursor: pointer;
  transition: transform 0.15s ease, box-shadow 0.15s ease;
}

.shop-card:active {
  transform: scale(0.99);
}

.shop-card__rank {
  flex: 0 0 28px;
  width: 28px;
  height: 28px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 700;
  font-size: var(--font-hint);
  color: var(--color-secondary);
  background: rgba(47, 79, 79, 0.08);
}

.shop-card__rank.is-top {
  color: #fff;
  background: var(--color-primary);
}

.shop-card__body {
  flex: 1;
  min-width: 0;
}

.shop-card__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}

.shop-card__name {
  margin: 0;
  font-size: var(--font-title);
  font-weight: 700;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.shop-card__score {
  flex: none;
  color: var(--color-primary);
  font-weight: 700;
}

.shop-card__meta {
  margin: 2px 0 8px;
}

.shop-card__dot {
  margin: 0 4px;
}

.shop-card__tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 8px;
}

.shop-card__dishes,
.shop-card__foot {
  margin: 0;
}

.shop-card__foot {
  margin-top: 6px;
}
</style>