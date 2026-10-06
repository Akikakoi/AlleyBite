<template>
  <div class="ab-page">
    <div class="ab-toolbar">
      <el-input
        v-model="actionFilter"
        placeholder="按 action 过滤"
        clearable
        style="width: 220px"
        @keyup.enter="load"
      />
      <el-button type="primary" :loading="loading" @click="load">查询</el-button>
      <el-button @click="reset">重置</el-button>
      <div class="ab-toolbar__spacer"></div>
      <el-button :loading="loading" @click="load">刷新</el-button>
    </div>

    <el-table v-loading="loading" :data="rows" border stripe>
      <el-table-column prop="id" label="ID" width="80" />
      <el-table-column prop="operator" label="操作人" width="140" show-overflow-tooltip />
      <el-table-column prop="action" label="动作" width="180" show-overflow-tooltip />
      <el-table-column prop="target_type" label="目标类型" width="130" show-overflow-tooltip />
      <el-table-column prop="target_id" label="目标 ID" width="110">
        <template #default="{ row }">{{ row.target_id ?? '-' }}</template>
      </el-table-column>
      <el-table-column label="变更前" min-width="220">
        <template #default="{ row }">
          <pre class="ab-json ab-json--cell">{{ prettyJson(row.before) }}</pre>
        </template>
      </el-table-column>
      <el-table-column label="变更后" min-width="220">
        <template #default="{ row }">
          <pre class="ab-json ab-json--cell">{{ prettyJson(row.after) }}</pre>
        </template>
      </el-table-column>
      <el-table-column label="时间" width="180">
        <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
      </el-table-column>
      <template #empty>
        <el-empty description="暂无审计记录" />
      </template>
    </el-table>
  </div>
</template>

<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { onMounted, ref } from 'vue'

import { listAudit } from '@/api/admin'
import { errorMessage } from '@/api/request'
import type { AuditItem } from '@/api/types'
import { formatTime, prettyJson } from '@/utils/format'

const rows = ref<AuditItem[]>([])
const actionFilter = ref('')
const loading = ref(false)

async function load() {
  loading.value = true
  try {
    rows.value = await listAudit(50, actionFilter.value.trim())
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}

function reset() {
  actionFilter.value = ''
  load()
}

onMounted(load)
</script>

<style scoped>
.ab-json--cell {
  max-height: 140px;
  margin: 0;
  font-size: 12px;
}
</style>