<template>
  <div class="users">
    <div class="users__toolbar">
      <el-button type="primary" @click="openCreate">新增账号</el-button>
      <span class="users__hint">仅超级管理员可见；新建账号的口令至少 6 位。</span>
    </div>

    <el-table v-loading="loading" :data="users" border stripe>
      <el-table-column prop="id" label="ID" width="64" />
      <el-table-column prop="username" label="账号" min-width="140" />
      <el-table-column label="角色" width="120">
        <template #default="{ row }">
          <el-tag :type="roleTagType(row.role)" effect="plain">{{ row.role }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag :type="row.is_active ? 'success' : 'info'" effect="plain">
            {{ row.is_active ? '启用' : '停用' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="最近登录" min-width="170">
        <template #default="{ row }">
          {{ row.last_login_at ? row.last_login_at.replace('T', ' ').slice(0, 19) : '从未登录' }}
        </template>
      </el-table-column>
      <el-table-column label="操作" width="120" fixed="right">
        <template #default="{ row }">
          <el-button size="small" text type="primary" @click="openReset(row)">重置口令</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog
      v-model="dialogVisible"
      :title="isReset ? `重置口令：${form.username}` : '新增账号'"
      width="420px"
    >
      <el-form label-width="80px">
        <el-form-item label="账号">
          <el-input v-model="form.username" :disabled="isReset" maxlength="64" />
        </el-form-item>
        <el-form-item label="口令">
          <el-input v-model="form.password" type="password" show-password maxlength="128" />
        </el-form-item>
        <el-form-item label="角色">
          <el-select v-model="form.role">
            <el-option label="superadmin（全部权限）" value="superadmin" />
            <el-option label="operator（可写运营）" value="operator" />
            <el-option label="reviewer（只读审核）" value="reviewer" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { onMounted, reactive, ref } from 'vue'

import { listUsers, upsertUser } from '@/api/admin'
import type { AdminUserItem, AdminUserUpsertPayload } from '@/api/types'

const users = ref<AdminUserItem[]>([])
const loading = ref(false)
const dialogVisible = ref(false)
const submitting = ref(false)
const isReset = ref(false)

const form = reactive({
  username: '',
  password: '',
  role: 'operator' as AdminUserUpsertPayload['role'],
})

function roleTagType(role: string) {
  if (role === 'superadmin') return 'danger'
  if (role === 'operator') return 'warning'
  return 'info'
}

async function load() {
  loading.value = true
  try {
    users.value = await listUsers()
  } finally {
    loading.value = false
  }
}

function openCreate() {
  isReset.value = false
  form.username = ''
  form.password = ''
  form.role = 'operator'
  dialogVisible.value = true
}

function openReset(row: AdminUserItem) {
  isReset.value = true
  form.username = row.username
  form.password = ''
  form.role = row.role as AdminUserUpsertPayload['role']
  dialogVisible.value = true
}

async function submit() {
  if (!form.username.trim() || form.password.length < 6) {
    ElMessage.warning('请填写账号名与至少 6 位的口令')
    return
  }
  submitting.value = true
  try {
    await upsertUser({
      username: form.username.trim(),
      password: form.password,
      role: form.role,
    })
    ElMessage.success('已保存')
    dialogVisible.value = false
    await load()
  } finally {
    submitting.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.users {
  padding: 16px;
}

.users__toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}

.users__hint {
  font-size: 12px;
  color: #909399;
}
</style>
