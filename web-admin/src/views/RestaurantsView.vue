<template>
  <div class="ab-page">
    <div class="ab-toolbar">
      <el-input
        v-model="filters.city"
        placeholder="城市"
        clearable
        style="width: 140px"
        @keyup.enter="search"
      />
      <el-select
        v-model="filters.status"
        placeholder="状态"
        clearable
        style="width: 140px"
      >
        <el-option label="营业中 active" value="active" />
        <el-option label="已屏蔽 blocked" value="blocked" />
        <el-option label="已归并 merged" value="merged" />
      </el-select>
      <el-input
        v-model="filters.q"
        placeholder="关键词（名称/别名）"
        clearable
        style="width: 220px"
        @keyup.enter="search"
      />
      <el-button type="primary" :loading="loading" @click="search">查询</el-button>
      <el-button @click="reset">重置</el-button>
      <div class="ab-toolbar__spacer"></div>
      <el-button :loading="loading" @click="load">刷新</el-button>
    </div>

    <el-table v-loading="loading" :data="rows" border stripe>
      <el-table-column prop="id" label="ID" width="80" />
      <el-table-column prop="name" label="店铺名" min-width="150" show-overflow-tooltip />
      <el-table-column prop="city" label="城市" width="90" />
      <el-table-column prop="area" label="区域" width="110" show-overflow-tooltip />
      <el-table-column prop="cuisine" label="菜系" width="100" show-overflow-tooltip />
      <el-table-column prop="address" label="地址" min-width="180" show-overflow-tooltip />
      <el-table-column label="均价" width="90" align="right">
        <template #default="{ row }">
          {{ row.avg_price === null || row.avg_price === undefined ? '-' : row.avg_price }}
        </template>
      </el-table-column>
      <el-table-column prop="mention_count" label="提及数" width="90" align="right" sortable />
      <el-table-column prop="alias_count" label="别名数" width="90" align="right" />
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag :type="statusTagType(row.status)" effect="light">
            {{ statusLabel(row.status) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="240" fixed="right">
        <template #default="{ row }">
          <el-button
            v-if="row.status === 'blocked'"
            type="success"
            size="small"
            @click="handleStatus(row, 'active')"
          >
            恢复
          </el-button>
          <el-button
            v-else
            type="warning"
            size="small"
            :disabled="row.status === 'merged'"
            @click="handleStatus(row, 'blocked')"
          >
            屏蔽
          </el-button>
          <el-button
            type="danger"
            size="small"
            plain
            :disabled="row.status === 'merged'"
            @click="openMerge(row)"
          >
            合并到…
          </el-button>
          <el-button type="primary" size="small" plain @click="openAlias(row)">
            添加别名
          </el-button>
        </template>
      </el-table-column>
      <template #empty>
        <el-empty description="暂无店铺数据" />
      </template>
    </el-table>

    <div class="ab-pagination">
      <el-pagination
        v-model:current-page="page"
        :page-size="pageSize"
        :total="total"
        layout="total, prev, pager, next, jumper"
        background
        @current-change="load"
      />
    </div>

    <el-dialog v-model="mergeVisible" title="合并店铺" width="440px">
      <p class="ab-dialog__hint">
        将
        <strong>{{ mergeRow?.name }}</strong>
        （ID {{ mergeRow?.id }}）的全部提及合并到目标店铺，操作不可撤销。
      </p>
      <el-form label-width="110px">
        <el-form-item label="目标店铺 ID">
          <el-input-number v-model="mergeTargetId" :min="1" :step="1" controls-position="right" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="mergeVisible = false">取消</el-button>
        <el-button type="danger" :loading="merging" @click="confirmMerge">确认合并</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="aliasVisible" title="添加别名" width="440px">
      <p class="ab-dialog__hint">
        为
        <strong>{{ aliasRow?.name }}</strong>
        （ID {{ aliasRow?.id }}）新增一个别名。
      </p>
      <el-form label-width="80px">
        <el-form-item label="别名">
          <el-input v-model="aliasValue" placeholder="请输入别名" maxlength="120" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="aliasVisible = false">取消</el-button>
        <el-button type="primary" :loading="aliasing" @click="confirmAlias">确认添加</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import { onMounted, reactive, ref } from 'vue'

import {
  addAlias,
  listRestaurants,
  mergeRestaurant,
  setRestaurantStatus,
} from '@/api/admin'
import { errorMessage } from '@/api/request'
import type { RestaurantItem } from '@/api/types'

const filters = reactive({ city: '', status: '' as string, q: '' })
const rows = ref<RestaurantItem[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = 20
const loading = ref(false)

const mergeVisible = ref(false)
const mergeRow = ref<RestaurantItem | null>(null)
const mergeTargetId = ref<number>(0)
const merging = ref(false)

const aliasVisible = ref(false)
const aliasRow = ref<RestaurantItem | null>(null)
const aliasValue = ref('')
const aliasing = ref(false)

function statusTagType(status: string): 'success' | 'danger' | 'info' {
  if (status === 'active') return 'success'
  if (status === 'blocked') return 'danger'
  return 'info'
}

function statusLabel(status: string): string {
  if (status === 'active') return '营业中'
  if (status === 'blocked') return '已屏蔽'
  if (status === 'merged') return '已归并'
  return status
}

async function load() {
  loading.value = true
  try {
    const result = await listRestaurants({
      city: filters.city.trim(),
      status: filters.status,
      q: filters.q.trim(),
      limit: pageSize,
      offset: (page.value - 1) * pageSize,
    })
    rows.value = result.items
    total.value = result.total
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}

function search() {
  page.value = 1
  load()
}

function reset() {
  filters.city = ''
  filters.status = ''
  filters.q = ''
  page.value = 1
  load()
}

async function handleStatus(row: RestaurantItem, status: 'active' | 'blocked') {
  const actionText = status === 'blocked' ? '屏蔽' : '恢复'
  try {
    await ElMessageBox.confirm(
      `确认${actionText}店铺「${row.name}」？`,
      `${actionText}店铺`,
      { type: 'warning', confirmButtonText: `确认${actionText}`, cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    const result = await setRestaurantStatus(row.id, status)
    ElMessage.success(`${actionText}成功（当前状态：${result.status}）`)
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

function openMerge(row: RestaurantItem) {
  mergeRow.value = row
  mergeTargetId.value = 0
  mergeVisible.value = true
}

async function confirmMerge() {
  const source = mergeRow.value
  const targetId = Number(mergeTargetId.value)
  if (!source) return
  if (!Number.isInteger(targetId) || targetId <= 0) {
    ElMessage.warning('请输入有效的目标店铺 ID')
    return
  }
  if (targetId === source.id) {
    ElMessage.warning('目标店铺不能与源店铺相同')
    return
  }
  try {
    await ElMessageBox.confirm(
      `将「${source.name}」(ID ${source.id}) 合并到 ID ${targetId}：全部提及与别名会迁移，且不可撤销。确认继续？`,
      '危险操作确认',
      { type: 'error', confirmButtonText: '确认合并', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  merging.value = true
  try {
    const result = await mergeRestaurant(source.id, targetId)
    ElMessage.success(
      `合并完成：迁移提及 ${result.mentions_moved} 条，状态 ${result.status}`,
    )
    mergeVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    merging.value = false
  }
}

function openAlias(row: RestaurantItem) {
  aliasRow.value = row
  aliasValue.value = ''
  aliasVisible.value = true
}

async function confirmAlias() {
  const source = aliasRow.value
  const alias = aliasValue.value.trim()
  if (!source) return
  if (!alias) {
    ElMessage.warning('请输入别名')
    return
  }
  aliasing.value = true
  try {
    const result = await addAlias(source.id, alias)
    if (result.created) {
      ElMessage.success(`别名「${result.alias}」已添加`)
      aliasVisible.value = false
      await load()
    } else {
      ElMessage.warning('别名已存在')
    }
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    aliasing.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.ab-pagination {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}

.ab-dialog__hint {
  margin: 0 0 16px;
  font-size: 13px;
  line-height: 1.6;
  color: #4a6b6b;
}
</style>