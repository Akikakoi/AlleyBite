import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { clearSession, getUsername, getToken, setUsername, setToken } from '@/utils/token'

/** C 端登录态（V2.0 账号体系）：token 持久化在 localStorage */
export const useUserStore = defineStore('user', () => {
  const token = ref(getToken())
  const username = ref(getUsername())

  const isLoggedIn = computed(() => !!token.value)

  function setSession(newToken: string, name: string) {
    token.value = newToken
    username.value = name
    setToken(newToken)
    setUsername(name)
  }

  function logout() {
    clearSession()
    token.value = ''
    username.value = ''
  }

  return { token, username, isLoggedIn, setSession, logout }
})
