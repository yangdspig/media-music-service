import { defineStore } from 'pinia'

const STORAGE_KEY = 'mm_theme'

export const useThemeStore = defineStore('theme', {
  state: () => ({
    theme: localStorage.getItem(STORAGE_KEY) || 'light',
  }),
  getters: {
    isDark: (state) => state.theme === 'dark',
  },
  actions: {
    init() {
      this.apply()
    },
    toggle() {
      this.theme = this.isDark ? 'light' : 'dark'
      localStorage.setItem(STORAGE_KEY, this.theme)
      this.apply()
    },
    apply() {
      const root = document.documentElement
      if (this.isDark) {
        root.setAttribute('data-theme', 'dark')
      } else {
        root.removeAttribute('data-theme')
      }
    },
  },
})
