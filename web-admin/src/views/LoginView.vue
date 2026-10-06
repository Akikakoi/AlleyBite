<template>
  <div class="login-page">
    <el-card class="login-card" shadow="always">
      <div class="login-card__head">
        <div class="login-card__logo">巷</div>
        <h1 class="login-card__title">管理后台</h1>
        <p class="login-card__sub">苍蝇馆子美食发现器</p>
      </div>
      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        label-position="top"
        @submit.prevent
      >
        <el-form-item label="账号" prop="username">
          <el-input
            v-model="form.username"
            placeholder="请输入账号"
            autocomplete="username"
            @keyup.enter="submit"
          />
        </el-form-item>
        <el-form-item label="口令" prop="password">
          <el-input
            v-model="form.password"
            type="password"
            show-password
            placeholder="请输入口令"
            autocomplete="current-password"
            @keyup.enter="submit"
          />
        </el-form-item>
        <el-button
          class="login-card__submit"
          type="primary"
          :loading="loading"
          @click="submit"
        >
          登录
        </el-button>
      </el-form>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { errorMessage } from '@/api/request'
import { useAuthStore } from '@/store/auth'

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()

const formRef = ref<FormInstance>()
const loading = ref(false)
const form = reactive({ username: '', password: '' })

const rules: FormRules = {
  username: [{ required: true, message: '请输入账号', trigger: 'blur' }],
  password: [{ required: true, message: '请输入口令', trigger: 'blur' }],
}

async function submit() {
  if (!formRef.value) return
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return
  loading.value = true
  try {
    await auth.login(form.username.trim(), form.password)
    ElMessage.success('登录成功')
    const redirect =
      typeof route.query.redirect === 'string' ? route.query.redirect : '/reviews'
    router.replace(redirect)
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-page {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100vh;
  background: linear-gradient(135deg, #2f4f4f 0%, #4a6b6b 55%, #ff8d63 100%);
}

.login-card {
  width: 380px;
  border-radius: 12px;
}

.login-card__head {
  text-align: center;
  margin-bottom: 20px;
}

.login-card__logo {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 48px;
  height: 48px;
  border-radius: 12px;
  background: #ff6b35;
  color: #fff;
  font-size: 24px;
  font-weight: 700;
}

.login-card__title {
  margin: 12px 0 4px;
  font-size: 20px;
  color: #2f4f4f;
}

.login-card__sub {
  margin: 0;
  font-size: 13px;
  color: #909399;
}

.login-card__submit {
  width: 100%;
  margin-top: 4px;
}
</style>