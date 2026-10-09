import { defineStore } from 'pinia'

const STORAGE_KEY = 'mm_api_key'
const ANONYMOUS_KEY = 'mm_anonymous_verified'

export const useAuthStore = defineStore('auth', {
  state: () => ({
    apiKey: localStorage.getItem(STORAGE_KEY) || '',
    anonymousVerified: sessionStorage.getItem(ANONYMOUS_KEY) === 'true',
  }),
  getters: {
    isAuthenticated: (state) => Boolean(state.apiKey) || state.anonymousVerified,
  },
  actions: {
    setKey(key) {
      this.apiKey = key
      this.anonymousVerified = false
      sessionStorage.removeItem(ANONYMOUS_KEY)
      if (key) {
        localStorage.setItem(STORAGE_KEY, key)
      } else {
        localStorage.removeItem(STORAGE_KEY)
      }
    },
    allowAnonymous() {
      this.setKey('')
      this.anonymousVerified = true
      sessionStorage.setItem(ANONYMOUS_KEY, 'true')
    },
    clear() {
      this.setKey('')
    },
  },
})
