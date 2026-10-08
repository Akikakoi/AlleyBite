/** C 端令牌存取（localStorage）：与 api/store 解耦，避免循环依赖 */

const TOKEN_KEY = 'alleybite.token'
const USERNAME_KEY = 'alleybite.username'

export function getToken(): string {
  return localStorage.getItem(TOKEN_KEY) || ''
}

export function setToken(token: string) {
  localStorage.setItem(TOKEN_KEY, token)
}

export function getUsername(): string {
  return localStorage.getItem(USERNAME_KEY) || ''
}

export function setUsername(name: string) {
  localStorage.setItem(USERNAME_KEY, name)
}

export function clearSession() {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USERNAME_KEY)
}
