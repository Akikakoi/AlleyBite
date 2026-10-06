<template>
  <div class="ab-page">
    <div class="ab-toolbar">
      <el-alert
        class="ab-toolbar__spacer"
        type="info"
        :closable="false"
        show-icon
        title="灰区归并待审队列：相似度处于阈值区间，需人工确认归并或驳回。"
      />
      <el-button :loading="loading" @click="load">刷新</el-button>
    </div>

    <el-table v-loading="loading" :data="rows" border stripe>
      <el-table-column prop="shop_name_raw" label="店铺名" min-width="160" show-overflow-tooltip />
      <el-table-column prop="city" label="城市" width="90" />
      <el-table-column prop="address_text" label="地址" min-width="180" show-overflow-tooltip />
      <el-table-column label="候选店铺" min-width="140">
        <template #default="{ row }">
          <span v-if="row.candidate_name">{{ row.candidate_name }}</span>
          <span v-else class="ab-muted">-</span>
        </template>
      </el-table-column>
      <el-table-column label="相似度" width="100" align="right">
        <template #default="{ row }">
          <el-tag :type="scoreTagType(row.score)" effect="plain">{{ score3(row.score) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="reason" label="原因" min-width="220" show-overflow-tooltip />
      <el-table-column prop="evidence_span" label="证据片段" min-width="220" show-overflow-tooltip />
      <el-table-column label="操作" width="180" fixed="right">
        <template #default="{ row }">
          <el-button
            type="primary"
            size="small"
            :loading="actingId === row.id"
            @click="handleConfirm(row)"
          >
            确认归并
          </el-button>
          <el-button
            type="danger"
            size="small"
            plain
            :disabled="actingId === row.id"
            @click="handleReject(row)"
          >
            驳回
          </el-button>
        </template>
      </el-table-column>
      <template #empty>
        <el-empty description="暂无待审记录" />
      </template>
    </el-table>
  </div>
</template>

<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import { onMounted, ref } from 'vue'

import { confirmReview, listReviews, rejectReview } from '@/api/admin'
import { errorMessage } from '@/api/request'
import type { ReviewItem } from '@/api/types'

const rows = ref<ReviewItem[]>([])
const loading = ref(false)
const actingId = ref<number | null>(null)

function score3(score: number): string {
  const value = typeof score === 'number' ? score : Number(score)
  return Number.isFinite(value) ? value.toFixed(3) : '-'
}

function scoreTagType(score: number): 'success' | 'warning' | 'danger' {
  if (score >= 0.85) return 'success'
  if (score >= 0.7) return 'warning'
  return 'danger'
}

async function load() {
  loading.value = true
  try {
    rows.value = await listReviews(100)
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}

async function handleConfirm(row: ReviewItem) {
  const target = row.candidate_name
    ? `候选店铺「${row.candidate_name}」`
    : '匹配到的候选店铺'
  try {
    await ElMessageBox.confirm(
      `确认将「${row.shop_name_raw}」归并到 ${target}？`,
      '确认归并',
      { type: 'warning', confirmButtonText: '确认归并', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  actingId.value = row.id
  try {
    await confirmReview(row.id, row.candidate_restaurant_id)
    ElMessage.success('已确认归并')
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    actingId.value = null
  }
}

async function handleReject(row: ReviewItem) {
  try {
    await ElMessageBox.confirm(
      `确认驳回「${row.shop_name_raw}」的归并候选？`,
      '确认驳回',
      { type: 'warning', confirmButtonText: '确认驳回', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  actingId.value = row.id
  try {
    await rejectReview(row.id)
    ElMessage.success('已驳回')
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    actingId.value = null
  }
}

onMounted(load)
</script>

<style scoped>
.ab-muted {
  color: #909399;
}
</style>