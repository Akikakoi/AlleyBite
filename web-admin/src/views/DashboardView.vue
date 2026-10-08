<template>
  <div class="dashboard">
    <div class="dashboard__head">
      <span class="dashboard__meta">统计窗口：近 {{ stats?.window_days ?? 14 }} 天</span>
      <el-button size="small" :loading="loading" @click="load">刷新</el-button>
    </div>

    <el-row v-if="stats" :gutter="12" class="dashboard__cards">
      <el-col v-for="card in overviewCards" :key="card.label" :xs="12" :sm="8" :md="4">
        <el-card shadow="never" class="stat-card">
          <div class="stat-card__label">{{ card.label }}</div>
          <div class="stat-card__value">{{ card.value }}</div>
        </el-card>
      </el-col>
    </el-row>

    <el-row v-if="stats" :gutter="12">
      <el-col :md="12" :xs="24">
        <el-card shadow="never" class="chart-card">
          <template #header>任务执行（近 {{ stats.window_days }} 天，成功 / 失败）</template>
          <div ref="jobsChartRef" class="chart"></div>
        </el-card>
      </el-col>
      <el-col :md="12" :xs="24">
        <el-card shadow="never" class="chart-card">
          <template #header>口碑证据增长（mention / 天，LLM 抽取产出）</template>
          <div ref="mentionsChartRef" class="chart"></div>
        </el-card>
      </el-col>
      <el-col :md="12" :xs="24">
        <el-card shadow="never" class="chart-card">
          <template #header>各城市店铺分布</template>
          <div ref="cityChartRef" class="chart"></div>
        </el-card>
      </el-col>
      <el-col :md="12" :xs="24">
        <el-card shadow="never" class="chart-card">
          <template #header>采集内容状态分布</template>
          <div ref="rawChartRef" class="chart"></div>
        </el-card>
      </el-col>
    </el-row>

    <el-card v-if="stats" shadow="never" class="quality-card">
      <template #header>抽取质量（文档 14 章验收口径）</template>
      <el-descriptions :column="4" border size="small">
        <el-descriptions-item label="已抽取内容">{{ stats.extract.extracted }}</el-descriptions-item>
        <el-descriptions-item label="抽取失败">{{ stats.extract.failed }}</el-descriptions-item>
        <el-descriptions-item label="抽取失败率">
          {{ percent(stats.extract.failure_rate) }}
        </el-descriptions-item>
        <el-descriptions-item label="平均置信度">
          {{ stats.extract.avg_confidence }}
        </el-descriptions-item>
        <el-descriptions-item label="地址完整率">
          {{ percent(stats.extract.address_coverage) }}
        </el-descriptions-item>
        <el-descriptions-item label="任务成功率（近窗口）">
          {{ percent(stats.jobs_summary.success_rate) }}
        </el-descriptions-item>
        <el-descriptions-item label="任务成功 / 失败">
          {{ stats.jobs_summary.success }} / {{ stats.jobs_summary.failed }}
        </el-descriptions-item>
        <el-descriptions-item label="快照生成时间">
          {{ formatTime(stats.generated_at) }}
        </el-descriptions-item>
      </el-descriptions>
    </el-card>

    <el-skeleton v-if="!stats && loading" :rows="6" animated class="dashboard__skeleton" />
    <el-empty v-if="!stats && !loading" description="暂无统计数据" />
  </div>
</template>

<script setup lang="ts">
import * as echarts from 'echarts'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import { getStats } from '@/api/admin'
import type { AdminStats } from '@/api/types'

const stats = ref<AdminStats | null>(null)
const loading = ref(false)

const jobsChartRef = ref<HTMLElement>()
const mentionsChartRef = ref<HTMLElement>()
const cityChartRef = ref<HTMLElement>()
const rawChartRef = ref<HTMLElement>()
let charts: echarts.ECharts[] = []

const overviewCards = computed(() => {
  if (!stats.value) return []
  const o = stats.value.overview
  return [
    { label: '城市', value: o.cities },
    { label: '店铺总数', value: o.restaurants_total },
    { label: '有效店铺', value: o.restaurants_active },
    { label: '口碑证据', value: o.mentions_total },
    { label: '采集内容', value: o.raw_total },
    { label: '抽取失败率', value: percent(stats.value?.extract.failure_rate ?? 0) },
  ]
})

function percent(value: number): string {
  return `${(value * 100).toFixed(1)}%`
}

function formatTime(iso: string): string {
  return iso ? iso.replace('T', ' ').slice(0, 19) : '-'
}

async function load() {
  loading.value = true
  try {
    stats.value = await getStats(14)
    renderCharts()
  } finally {
    loading.value = false
  }
}

function renderCharts() {
  if (!stats.value) return
  charts.forEach((c) => c.dispose())
  charts = []

  if (jobsChartRef.value) {
    const chart = echarts.init(jobsChartRef.value)
    chart.setOption({
      tooltip: { trigger: 'axis' },
      legend: { data: ['成功', '失败'] },
      grid: { left: 40, right: 16, top: 32, bottom: 24 },
      xAxis: { type: 'category', data: stats.value.jobs_14d.map((d) => d.date.slice(5)) },
      yAxis: { type: 'value', minInterval: 1 },
      series: [
        { name: '成功', type: 'bar', stack: 'jobs', itemStyle: { color: '#67c23a' }, data: stats.value.jobs_14d.map((d) => d.success ?? 0) },
        { name: '失败', type: 'bar', stack: 'jobs', itemStyle: { color: '#f56c6c' }, data: stats.value.jobs_14d.map((d) => d.failed ?? 0) },
      ],
    })
    charts.push(chart)
  }

  if (mentionsChartRef.value) {
    const chart = echarts.init(mentionsChartRef.value)
    chart.setOption({
      tooltip: { trigger: 'axis' },
      grid: { left: 40, right: 16, top: 16, bottom: 24 },
      xAxis: { type: 'category', data: stats.value.mentions_14d.map((d) => d.date.slice(5)) },
      yAxis: { type: 'value', minInterval: 1 },
      series: [
        {
          name: '新增 mention',
          type: 'line',
          smooth: true,
          itemStyle: { color: '#ff6b35' },
          areaStyle: { opacity: 0.15 },
          data: stats.value.mentions_14d.map((d) => d.count ?? 0),
        },
      ],
    })
    charts.push(chart)
  }

  if (cityChartRef.value) {
    const chart = echarts.init(cityChartRef.value)
    const rows = stats.value.city_restaurants
    chart.setOption({
      tooltip: { trigger: 'axis' },
      legend: { data: ['有效店铺', '全部'] },
      grid: { left: 40, right: 16, top: 32, bottom: 24 },
      xAxis: { type: 'category', data: rows.map((r) => r.city) },
      yAxis: { type: 'value', minInterval: 1 },
      series: [
        { name: '有效店铺', type: 'bar', itemStyle: { color: '#ff6b35' }, data: rows.map((r) => r.active) },
        { name: '全部', type: 'bar', itemStyle: { color: '#2f4f4f' }, data: rows.map((r) => r.total) },
      ],
    })
    charts.push(chart)
  }

  if (rawChartRef.value) {
    const chart = echarts.init(rawChartRef.value)
    chart.setOption({
      tooltip: { trigger: 'item' },
      legend: { bottom: 0 },
      series: [
        {
          type: 'pie',
          radius: ['40%', '65%'],
          center: ['50%', '45%'],
          label: { formatter: '{b}: {c}' },
          data: stats.value.raw_status.map((r) => ({ name: r.status, value: r.count })),
        },
      ],
    })
    charts.push(chart)
  }
}

function handleResize() {
  charts.forEach((c) => c.resize())
}

onMounted(() => {
  load()
  window.addEventListener('resize', handleResize)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  charts.forEach((c) => c.dispose())
  charts = []
})
</script>

<style scoped>
.dashboard {
  padding: 16px;
}

.dashboard__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}

.dashboard__meta {
  font-size: 13px;
  color: #909399;
}

.dashboard__cards {
  margin-bottom: 12px;
}

.stat-card__label {
  font-size: 12px;
  color: #909399;
}

.stat-card__value {
  margin-top: 6px;
  font-size: 24px;
  font-weight: 700;
  color: #2f4f4f;
}

.chart-card {
  margin-bottom: 12px;
}

.chart {
  width: 100%;
  height: 280px;
}

.quality-card {
  margin-bottom: 12px;
}

.dashboard__skeleton {
  padding: 16px;
}
</style>
