<template>
  <div class="ab-page">
    <div class="ab-toolbar">
      <el-select
        v-model="statusFilter"
        placeholder="全部状态"
        clearable
        style="width: 160px"
        @change="load"
      >
        <el-option
          v-for="opt in statusOptions"
          :key="opt.value"
          :label="opt.label"
          :value="opt.value"
        />
      </el-select>
      <el-button type="primary" :loading="loading" @click="load">查询</el-button>
      <div class="ab-toolbar__spacer"></div>
      <el-button :loading="loading" @click="load">刷新</el-button>
    </div>

    <el-table v-loading="loading" :data="rows" border stripe>
      <el-table-column prop="id" label="ID" width="80" />
      <el-table-column prop="restaurant_id" label="店铺 ID" width="100">
        <template #default="{ row }">{{ row.restaurant_id ?? '-' }}</template>
      </el-table-column>
      <el-table-column prop="type" label="类型" width="120" show-overflow-tooltip />
      <el-table-column prop="content" label="内容" min-width="240" show-overflow-tooltip />
      <el-table-column prop="contact" label="联系方式" width="160" show-overflow-tooltip>
        <template #default="{ row }">{{ row.contact || '-' }}</template>
      </el-table-column>
      <el-table-column label="状态" width="110">
        <template #default="{ row }">
          <el-tag :type="statusTagType(row.status)" effect="light">
            {{ statusLabel(row.status) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="创建时间" width="180">
        <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="160" fixed="right">
        <template #default="{ row }">
          <el-dropdown @command="handleCommand(row)">
            <el-button type="primary" size="small" plain>
              流转状态
              <el-icon><ArrowDown /></el-icon>
            </el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item
                  v-for="opt in statusOptions"
                  :key="opt.value"
                  :command="opt.value"
                  :disabled="opt.value === row.status"
                >
                  {{ opt.label }}
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </template>
      </el-table-column>
      <template #empty>
        <el-empty description="暂无反馈工单" />
      </template>
    </el-table>
  </div>
</template>

<script setup lang="ts">
import { ArrowDown } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { onMounted, ref } from 'vue'

import { listFeedback, updateFeedback } from '@/api/admin'
import { errorMessage } from '@/api/request'
import type { FeedbackItem, FeedbackStatus } from '@/api/types'
import { formatTime } from '@/utils/format'

const STATUS_OPTIONS: { label: string; value: FeedbackStatus }[] = [
  { label: '待处理', value: 'pending' },
  { label: '处理中', value: 'processing' },
  { label: '已解决', value: 'resolved' },
  { label: '已驳回', value: 'rejected' },
]

const statusOptions = STATUS_OPTIONS
const rows = ref<FeedbackItem[]>([])
const statusFilter = ref<string>('')
const loading = ref(false)

function statusLabel(status: string): string {
  return STATUS_OPTIONS.find((opt) => opt.value === status)?.label || status
}

function statusTagType(status: string): 'warning' | 'primary' | 'success' | 'danger' | 'info' {
  if (status === 'pending') return 'warning'
  if (status === 'processing') return 'primary'
  if (status === 'resolved') return 'success'
  if (status === 'rejected') return 'danger'
  return 'info'
}

async function load() {
  loading.value = true
  try {
    rows.value = await listFeedback(50, statusFilter.value)
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}

function handleCommand(row: FeedbackItem) {
  return (command: string | number | object) => {
    void transition(row, String(command) as FeedbackStatus)
  }
}

async function transition(row: FeedbackItem, status: FeedbackStatus) {
  if (status === row.status) return
  try {
    await ElMessageBox.confirm(
      `将工单 #${row.id} 状态流转为「${statusLabel(status)}」？`,
      '状态流转确认',
      { type: 'warning', confirmButtonText: '确认', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    const updated = await updateFeedback(row.id, status)
    ElMessage.success(`已更新为「${statusLabel(updated.status)}」`)
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

onMounted(load)
</script>