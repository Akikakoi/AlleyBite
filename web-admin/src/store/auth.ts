import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { login as loginApi } from '@/api/admin'
import { TOKEN_KEY } from '@/api/request'

const USER_KEY = 'alleybite.admin.user'

interface AdminUser {
  username: string
  role: string
}

function readUser(): AdminUser {
  try {
    const raw = localStorage.getItem(USER_KEY)
    if (!raw) return { username: '', role: '' }
    const parsed = JSON.parse(raw) as Partial<AdminUser>
    return { username: parsed.username || '', role: parsed.role || '' }
  } catch {
    return { username: '', role: '' }
  }
}

export const useAuthStore = defineStore('auth', () => {
  const token = ref(localStorage.getItem(TOKEN_KEY) || '')
  const stored = readUser()
  const username = ref(stored.username)
  const role = ref(stored.role)

  const isLoggedIn = computed(() => Boolean(token.value))

  function persistUser() {
    localStorage.setItem(
      USER_KEY,
      JSON.stringify({ username: username.value, role: role.value }),
    )
  }

  function setSession(payload: {
    token: string
    username: string
    role: string
  }) {
    token.value = payload.token
    username.value = payload.username
    role.value = payload.role
    localStorage.setItem(TOKEN_KEY, payload.token)
    persistUser()
  }

  function clear() {
    token.value = ''
    username.value = ''
    role.value = ''
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(USER_KEY)
  }

  async function login(name: string, password: string) {
    const result = await loginApi(name, password)
    setSession({
      token: result.token,
      username: result.username,
      role: result.role,
    })
    return result
  }

  return { token, username, role, isLoggedIn, setSession, clear, login }
})