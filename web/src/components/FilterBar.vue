<template>
  <van-dropdown-menu class="filter-bar" active-color="#FF6B35">
    <van-dropdown-item v-model="timeValue" :options="TIME_OPTIONS" @change="emitChange" />
    <van-dropdown-item
      v-model="cuisineValue"
      :options="cuisineOptions"
      @change="emitChange"
    />
    <van-dropdown-item
      v-model="priceValue"
      :options="PRICE_OPTIONS"
      @change="emitChange"
    />
    <van-dropdown-item v-model="areaValue" :options="areaOptions" @change="emitChange" />
  </van-dropdown-menu>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import type { RankFilters } from '@/types'

const props = defineProps<{
  filters: RankFilters
  cuisines: string[]
  areas: string[]
}>()

const emit = defineEmits<{ (e: 'change', filters: RankFilters): void }>()

const TIME_OPTIONS = [
  { text: '全部时间', value: '' },
  { text: '近 90 天', value: '90' },
]

const PRICE_OPTIONS = [
  { text: '人均不限', value: '' },
  { text: '¥30 以下', value: '0-30' },
  { text: '¥30-60', value: '30-60' },
  { text: '¥60-100', value: '60-100' },
  { text: '¥100 以上', value: '100-' },
]

const timeValue = ref(props.filters.days ? String(props.filters.days) : '')
const cuisineValue = ref(props.filters.cuisine ?? '')
const priceValue = ref(rangeToValue(props.filters))
const areaValue = ref(props.filters.area ?? '')

const cuisineOptions = computed(() => [
  { text: '菜系不限', value: '' },
  ...props.cuisines.map((item) => ({ text: item, value: item })),
])

const areaOptions = computed(() => [
  { text: '区域不限', value: '' },
  ...props.areas.map((item) => ({ text: item, value: item })),
])

function rangeToValue(filters: RankFilters): string {
  if (filters.priceMin === undefined && filters.priceMax === undefined) return ''
  return `${filters.priceMin ?? ''}-${filters.priceMax ?? ''}`
}

function valueToRange(value: string): Pick<RankFilters, 'priceMin' | 'priceMax'> {
  if (!value) return {}
  const [min, max] = value.split('-')
  return {
    priceMin: min ? Number(min) : undefined,
    priceMax: max ? Number(max) : undefined,
  }
}

function emitChange() {
  emit('change', {
    cuisine: cuisineValue.value || undefined,
    area: areaValue.value || undefined,
    days: timeValue.value ? Number(timeValue.value) : undefined,
    ...valueToRange(priceValue.value),
  })
}

watch(
  () => props.filters,
  (next) => {
    timeValue.value = next.days ? String(next.days) : ''
    cuisineValue.value = next.cuisine ?? ''
    areaValue.value = next.area ?? ''
    priceValue.value = rangeToValue(next)
  },
)
</script>

<style scoped>
.filter-bar {
  margin: 12px 0;
  border-radius: var(--radius-card);
  overflow: hidden;
  box-shadow: var(--shadow-card);
}
</style>