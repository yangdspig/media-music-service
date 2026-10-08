import { defineStore } from 'pinia'

let nextId = 1

export const useToastStore = defineStore('toast', {
  state: () => ({
    toasts: [],
  }),
  actions: {
    show(message, type = 'success', duration = 3000) {
      const id = nextId++
      this.toasts.push({ id, message, type })
      setTimeout(() => this.remove(id), duration)
      return id
    },
    remove(id) {
      this.toasts = this.toasts.filter((t) => t.id !== id)
    },
  },
})
