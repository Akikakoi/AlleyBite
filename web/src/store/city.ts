import { defineStore } from 'pinia'
import { ref } from 'vue'

const CITY_KEY = 'alleybite.city'
const RECENT_KEY = 'alleybite.recent'
const RECENT_MAX = 6

function readRecent(): string[] {
  try {
    const raw = localStorage.getItem(RECENT_KEY)
    const parsed: unknown = raw ? JSON.parse(raw) : []
    return Array.isArray(parsed)
      ? parsed.filter((item): item is string => typeof item === 'string')
      : []
  } catch {
    return []
  }
}

export const useCityStore = defineStore('city', () => {
  const current = ref(localStorage.getItem(CITY_KEY) || '')
  const recent = ref<string[]>(readRecent())

  function setCity(name: string) {
    const value = name.trim()
    if (!value) return
    current.value = value
    recent.value = [value, ...recent.value.filter((c) => c !== value)].slice(
      0,
      RECENT_MAX,
    )
    localStorage.setItem(CITY_KEY, value)
    localStorage.setItem(RECENT_KEY, JSON.stringify(recent.value))
  }

  return { current, recent, setCity }
})