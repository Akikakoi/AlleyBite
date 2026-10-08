<template>
  <div class="page login">
    <header class="login__bar">
      <button class="login__back" aria-label="返回" @click="router.back()">‹</button>
      <span class="text-sub">{{ mode === 'login' ? '登录' : '注册' }}</span>
    </header>

    <section class="card login__card">
      <h1 class="page-title">欢迎来到苍蝇馆子美食发现器</h1>
      <p class="text-sub login__sub">登录后可以收藏店铺，随时回来翻榜单</p>

      <van-tabs v-model:active="mode" shrink>
        <van-tab title="登录" name="login" />
        <van-tab title="注册" name="register" />
      </van-tabs>

      <van-form class="login__form" @submit.prevent="submit">
        <van-field
          v-model="form.username"
          name="username"
          label="用户名"
          maxlength="32"
          placeholder="2-32 位中文、字母、数字或下划线"
          :rules="[{ required: true, message: '请输入用户名' }]"
        />
        <van-field
          v-model="form.password"
          type="password"
          name="password"
          label="密码"
          maxlength="64"
          placeholder="至少 6 位"
          :rules="[{ required: true, message: '请输入密码' }]"
        />
        <van-button
          class="login__submit"
          type="primary"
          round
          block
          :loading="submitting"
          native-type="submit"
        >
          {{ mode === 'login' ? '登录' : '注册并登录' }}
        </van-button>
      </van-form>

      <p class="text-sub login__note">仅用于收藏功能，不收集其他个人信息</p>
    </section>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { showToast } from 'vant'

import { login, register } from '@/api/auth'
import { ApiError } from '@/api/request'
import { useUserStore } from '@/store/user'

const route = useRoute()
const router = useRouter()
const store = useUserStore()

const mode = ref<'login' | 'register'>('login')
const submitting = ref(false)
const form = ref({ username: '', password: '' })

async function submit() {
  const username = form.value.username.trim()
  const password = form.value.password
  if (!username || !password) return
  if (password.length < 6) {
    showToast('密码至少 6 位')
    return
  }
  submitting.value = true
  try {
    const action = mode.value === 'login' ? login : register
    const data = await action({ username, password })
    store.setSession(data.token, data.username)
    showToast(mode.value === 'login' ? '已登录' : '注册成功')
    const redirect = String(route.query.redirect || '/mine')
    router.push(redirect)
  } catch (err) {
    showToast(err instanceof ApiError ? err.message : '操作失败，请稍后重试')
  } finally {
    submitting.value = false
  }
}
</script>

<style scoped>
.login {
  padding-top: 16px;
}

.login__bar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}

.login__back {
  width: 32px;
  height: 32px;
  border: none;
  border-radius: 50%;
  background: var(--color-surface);
  box-shadow: var(--shadow-card);
  font-size: 20px;
  line-height: 1;
  cursor: pointer;
}

.login__card {
  padding: 20px 16px 24px;
}

.login__sub {
  margin: 4px 0 16px;
}

.login__form {
  margin-top: 12px;
}

.login__submit {
  margin-top: 20px;
}

.login__note {
  margin: 14px 0 0;
  text-align: center;
}
</style>
