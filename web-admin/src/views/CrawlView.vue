<template>
  <div class="ab-page">
    <el-alert
      v-if="tripped.length"
      class="ab-breaker"
      type="error"
      show-icon
      :closable="false"
      title="数据源已熔断（连续失败超过阈值，暂停采集）"
    >
      <div class="ab-breaker__body">
        <el-tag v-for="item in tripped" :key="item" type="danger" effect="dark">
          {{ item }}
        </el-tag>
      </div>
    </el-alert>

    <el-card class="ab-card" shadow="never">
      <template #header>
        <div class="ab-card__header">
          <span>手动触发采集</span>
          <span class="ab-card__note">同步长任务，可能需要数分钟，请勿重复提交</span>
        </div>
      </template>
      <el-form :inline="true" label-width="90px" @submit.prevent>
        <el-form-item label="城市">
          <el-input v-model="form.city" placeholder="留空表示全部城市" clearable style="width: 180px" />
        </el-form-item>
        <el-form-item label="数据源">
          <el-select
            v-model="form.sources"
            multiple
            filterable
            allow-create
            default-first-option
            placeholder="留空表示全部数据源"
            style="width: 260px"
          >
            <el-option v-for="key in sourceOptions" :key="key" :label="key" :value="key" />
          </el-select>
        </el-form-item>
        <el-form-item label="模式">
          <el-radio-group v-model="form.mode">
            <el-radio-button value="incremental">增量 incremental</el-radio-button>
            <el-radio-button value="full">全量 full</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="忽略夜间暂停">
          <el-switch v-model="form.force" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="running" @click="submit">
            {{ running ? '采集中…' : '触发采集' }}
          </el-button>
        </el-form-item>
      </el-form>

      <el-alert
        v-if="runResult"
        class="ab-run-result"
        :type="runResult.error ? 'error' : 'success'"
        show-icon
        :closable="false"
        :title="`任务 #${runResult.job_id} ${runResult.job_type} 状态：${runResult.status}`"
      >
        <div v-if="runResult.error" class="ab-run-result__error">{{ runResult.error }}</div>
        <pre class="ab-json">{{ prettyJson(runResult.stats) }}</pre>
      </el-alert>
    </el-card>

    <el-card class="ab-card" shadow="never">
      <template #header>
        <div class="ab-card__header">
          <span>最近采集任务</span>
          <el-button size="small" :loading="loading" @click="load">刷新</el-button>
        </div>
      </template>
      <el-table v-loading="loading" :data="jobs" border stripe>
        <el-table-column type="expand">
          <template #default="props">
            <div class="ab-expand">
              <div class="ab-expand__title">stats</div>
              <pre class="ab-json">{{ prettyJson(props.row.stats) }}</pre>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="id" label="ID" width="80" />
        <el-table-column prop="job_type" label="类型" width="140" />
        <el-table-column prop="city" label="城市" width="110">
          <template #default="{ row }">{{ row.city || '-' }}</template>
        </el-table-column>
        <el-table-column label="状态" width="120">
          <template #default="{ row }">
            <el-tag :type="jobStatusType(row.status)" effect="light">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="开始时间" width="180">
          <template #default="{ row }">{{ formatTime(row.started_at) }}</template>
        </el-table-column>
        <el-table-column label="结束时间" width="180">
          <template #default="{ row }">{{ formatTime(row.finished_at) }}</template>
        </el-table-column>
        <el-table-column label="错误" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">{{ row.error || '-' }}</template>
        </el-table-column>
        <template #empty>
          <el-empty description="暂无采集任务" />
        </template>
      </el-table>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onMounted, reactive, ref } from 'vue'

import { getCrawlOverview, runCrawl } from '@/api/admin'
import { errorMessage } from '@/api/request'
import type { CrawlJob, CrawlRunResult } from '@/api/types'
import { formatTime, prettyJson } from '@/utils/format'

const jobs = ref<CrawlJob[]>([])
const tripped = ref<string[]>([])
const sources = ref<Record<string, unknown>>({})
const loading = ref(false)

const running = ref(false)
const runResult = ref<CrawlRunResult | null>(null)

const form = reactive({
  city: '',
  sources: [] as string[],
  mode: 'incremental' as 'incremental' | 'full',
  force: false,
})

const sourceOptions = computed(() => Object.keys(sources.value))

function jobStatusType(status: string): 'success' | 'danger' | 'warning' | 'info' {
  const value = status.toLowerCase()
  if (['success', 'succeeded', 'done', 'finished', 'ok'].includes(value)) return 'success'
  if (['failed', 'error'].includes(value)) return 'danger'
  if (['running', 'pending', 'queued'].includes(value)) return 'warning'
  return 'info'
}

async function load() {
  loading.value = true
  try {
    const result = await getCrawlOverview(20)
    jobs.value = result.jobs
    tripped.value = result.breaker.tripped
    sources.value = result.breaker.sources
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}

async function submit() {
  const modeText = form.mode === 'full' ? '全量' : '增量'
  const cityText = form.city.trim() || '全部城市'
  const sourceText = form.sources.length ? form.sources.join('、') : '全部数据源'
  const forceText = form.force ? '，忽略夜间暂停' : ''
  try {
    await ElMessageBox.confirm(
      `将以「${modeText}」模式采集：${cityText}，数据源：${sourceText}${forceText}。该任务为同步长任务，可能耗时数分钟，确认触发？`,
      '触发采集确认',
      { type: 'warning', confirmButtonText: '确认触发', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  running.value = true
  runResult.value = null
  try {
    runResult.value = await runCrawl({
      city: form.city.trim() || undefined,
      sources: form.sources.length ? form.sources : undefined,
      mode: form.mode,
      force: form.force,
    })
    ElMessage.success(`采集任务 #${runResult.value.job_id} 已结束`)
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    running.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.ab-breaker {
  margin-bottom: 16px;
}

.ab-breaker__body {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 8px;
}

.ab-card {
  margin-bottom: 16px;
}

.ab-card__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.ab-card__note {
  font-size: 12px;
  color: #909399;
}

.ab-run-result {
  margin-top: 8px;
}

.ab-run-result__error {
  margin-bottom: 8px;
  color: #f56c6c;
}

.ab-expand {
  padding: 8px 16px;
}

.ab-expand__title {
  margin-bottom: 6px;
  font-size: 12px;
  font-weight: 600;
  color: #2f4f4f;
}
</style>