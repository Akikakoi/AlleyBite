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
        <el-option label="待审核" value="pending" />
        <el-option label="已展示" value="approved" />
        <el-option label="已拒绝" value="rejected" />
      </el-select>
      <el-button type="primary" :loading="loading" @click="load">查询</el-button>
      <div class="ab-toolbar__spacer"></div>
      <el-button :loading="loading" @click="load">刷新</el-button>
    </div>

    <el-table v-loading="loading" :data="rows" border stripe>
      <el-table-column prop="id" label="ID" width="80" />
      <el-table-column label="店铺" min-width="140" show-overflow-tooltip>
        <template #default="{ row }">
          {{ row.restaurant_name || `#${row.restaurant_id}` }}
        </template>
      </el-table-column>
      <el-table-column prop="username" label="用户" width="140" show-overflow-tooltip />
      <el-table-column prop="content" label="内容" min-width="220" show-overflow-tooltip />
      <el-table-column label="图片" width="160">
        <template #default="{ row }">
          <div v-if="row.images.length" class="ugc-thumbs">
            <el-image
              v-for="(img, idx) in row.images"
              :key="idx"
              :src="img"
              :preview-src-list="row.images"
              :initial-index="idx"
              fit="cover"
              class="ugc-thumbs__img"
              preview-teleported
            />
          </div>
          <span v-else>-</span>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="110">
        <template #default="{ row }">
          <el-tag :type="statusTagType(row.status)" effect="light">
            {{ statusLabel(row.status) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="发布时间" width="180">
        <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="180" fixed="right">
        <template #default="{ row }">
          <el-button
            type="success"
            size="small"
            :disabled="row.status === 'approved'"
            @click="review(row, 'approved')"
          >
            通过
          </el-button>
          <el-button
            type="danger"
            size="small"
            plain
            :disabled="row.status === 'rejected'"
            @click="review(row, 'rejected')"
          >
            拒绝
          </el-button>
        </template>
      </el-table-column>
      <template #empty>
        <el-empty description="暂无打卡内容" />
      </template>
    </el-table>
  </div>
</template>

<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import { onMounted, ref } from 'vue'

import { listUgc, reviewUgc } from '@/api/admin'
import { errorMessage } from '@/api/request'
import type { UgcItem, UgcStatus } from '@/api/types'
import { formatTime } from '@/utils/format'

const loading = ref(false)
const rows = ref<UgcItem[]>([])
const statusFilter = ref<UgcStatus | ''>('pending')

function statusLabel(status: string) {
  if (status === 'approved') return '已展示'
  if (status === 'rejected') return '已拒绝'
  return '待审核'
}

function statusTagType(status: string) {
  if (status === 'approved') return 'success'
  if (status === 'rejected') return 'danger'
  return 'warning'
}

async function load() {
  loading.value = true
  try {
    rows.value = await listUgc({
      status: statusFilter.value || undefined,
    })
  } catch (err) {
    ElMessage.error(errorMessage(err))
  } finally {
    loading.value = false
  }
}

async function review(row: UgcItem, status: 'approved' | 'rejected') {
  const action = status === 'approved' ? '通过并展示' : '拒绝展示'
  try {
    await ElMessageBox.confirm(
      `确定${action}该条打卡？（ID ${row.id}）`,
      'UGC 审核',
      { type: 'warning' },
    )
  } catch {
    return
  }
  try {
    await reviewUgc(row.id, status)
    ElMessage.success('已更新')
    await load()
  } catch (err) {
    ElMessage.error(errorMessage(err))
  }
}

onMounted(load)
</script>

<style scoped>
.ugc-thumbs {
  display: flex;
  gap: 4px;
}

.ugc-thumbs__img {
  width: 40px;
  height: 40px;
  border-radius: 4px;
}
</style>
