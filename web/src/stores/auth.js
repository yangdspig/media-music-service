import { defineStore } from 'pinia'

const STORAGE_KEY = 'mm_api_key'

export const useAuthStore = defineStore('auth', {
  state: () => ({
    apiKey: localStorage.getItem(STORAGE_KEY) || '',
  }),
  getters: {
    isAuthenticated: (state) => Boolean(state.apiKey),
  },
  actions: {
    setKey(key) {
      this.apiKey = key
      if (key) {
        localStorage.setItem(STORAGE_KEY, key)
      } else {
        localStorage.removeItem(STORAGE_KEY)
      }
    },
    clear() {
      this.setKey('')
    },
  },
})
