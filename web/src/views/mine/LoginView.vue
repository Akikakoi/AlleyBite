<template>
  <div class="page login">
    <header class="login__bar">
      <button class="login__back" aria-label="返回" @click="router.back()">‹</button>
      <span class="text-sub">{{ titleText }}</span>
    </header>

    <section class="card login__card">
      <h1 class="page-title">欢迎来到苍蝇馆子美食发现器</h1>
      <p class="text-sub login__sub">登录后可以收藏店铺、发布打卡，随时回来翻榜单</p>

      <van-tabs v-model:active="active" shrink>
        <van-tab title="登录" name="login" />
        <van-tab title="注册" name="register" />
        <van-tab title="验证码登录" name="sms" />
      </van-tabs>

      <!-- van-form 的 submit 事件参数是表单值对象而非 Event，不能加 .prevent
           （Vant 内部已阻止原生默认提交），否则报 preventDefault is not a function -->
      <van-form v-if="active !== 'sms'" class="login__form" @submit="submit">
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
          {{ active === 'login' ? '登录' : '注册并登录' }}
        </van-button>
      </van-form>

      <van-form v-else class="login__form" @submit="submitSms">
        <van-field
          v-model="smsForm.email"
          name="email"
          label="邮箱"
          maxlength="254"
          placeholder="邮箱地址"
          :rules="[
            { required: true, message: '请输入邮箱' },
            { pattern: /^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$/, message: '邮箱格式不正确' },
          ]"
        />
        <van-field
          v-model="smsForm.code"
          name="code"
          label="验证码"
          type="digit"
          maxlength="6"
          placeholder="6 位验证码"
          :rules="[{ required: true, message: '请输入验证码' }]"
        >
          <template #button>
            <van-button
              size="small"
              round
              plain
              type="primary"
              :disabled="countdown > 0"
              :loading="sending"
              @click="onSendCode"
            >
              {{ countdown > 0 ? `${countdown}s 后重发` : '发送验证码' }}
            </van-button>
          </template>
        </van-field>
        <p v-if="mockCode" class="text-sub login__devcode">
          本地联调验证码：{{ mockCode }}
        </p>
        <van-button
          class="login__submit"
          type="primary"
          round
          block
          :loading="submitting"
          native-type="submit"
        >
          登录
        </van-button>
      </van-form>

      <p class="text-sub login__note">仅用于收藏与打卡功能，不收集其他个人信息</p>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { showToast } from 'vant'

import { emailLogin, login, register, sendEmailCode } from '@/api/auth'
import { ApiError } from '@/api/request'
import { useUserStore } from '@/store/user'

const route = useRoute()
const router = useRouter()
const store = useUserStore()

const active = ref<'login' | 'register' | 'sms'>('login')
const submitting = ref(false)
const form = ref({ username: '', password: '' })
const smsForm = ref({ email: '', code: '' })
const sending = ref(false)
const countdown = ref(0)
const mockCode = ref('')

const titleText = computed(() =>
  active.value === 'login' ? '登录' : active.value === 'register' ? '注册' : '验证码登录',
)

let timer: number | undefined
function startCountdown(seconds: number) {
  countdown.value = seconds
  timer = window.setInterval(() => {
    countdown.value -= 1
    if (countdown.value <= 0 && timer) window.clearInterval(timer)
  }, 1000)
}
onUnmounted(() => {
  if (timer) window.clearInterval(timer)
})

async function onSendCode() {
  const email = smsForm.value.email.trim()
  if (!/^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$/.test(email)) {
    showToast('请先填写正确的邮箱')
    return
  }
  sending.value = true
  try {
    const data = await sendEmailCode(email)
    startCountdown(60)
    if (data.dev_code) mockCode.value = data.dev_code
    showToast('验证码已发送，请查收邮箱')
  } catch (err) {
    showToast(err instanceof ApiError ? err.message : '发送失败，请稍后重试')
  } finally {
    sending.value = false
  }
}

async function submitSms() {
  const email = smsForm.value.email.trim()
  const code = smsForm.value.code.trim()
  if (!email || !code) return
  submitting.value = true
  try {
    const data = await emailLogin({ email, code })
    store.setSession(data.token, data.username)
    showToast(data.created ? '注册成功' : '已登录')
    router.push(String(route.query.redirect || '/mine'))
  } catch (err) {
    showToast(err instanceof ApiError ? err.message : '登录失败，请稍后重试')
  } finally {
    submitting.value = false
  }
}

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
    const action = active.value === 'login' ? login : register
    const data = await action({ username, password })
    store.setSession(data.token, data.username)
    showToast(active.value === 'login' ? '已登录' : '注册成功')
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

.login__devcode {
  margin: 8px 16px 0;
}

.login__note {
  margin: 14px 0 0;
  text-align: center;
}
</style>
