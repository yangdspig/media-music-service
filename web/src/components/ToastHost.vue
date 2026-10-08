<template>
  <div class="fixed bottom-6 right-6 z-[100] flex flex-col gap-2 items-end">
    <TransitionGroup name="toast">
      <div
        v-for="toast in store.toasts"
        :key="toast.id"
        class="flex items-center gap-2 rounded-md border border-border bg-popover px-4 py-2.5 text-sm text-popover-foreground shadow-lg"
        :style="{ boxShadow: 'var(--mm-shadow-md)' }"
        role="status"
      >
        <CheckCircle2 v-if="toast.type === 'success'" class="w-4 h-4 shrink-0" :style="{ color: 'var(--state-success)' }" />
        <AlertTriangle v-else-if="toast.type === 'warning'" class="w-4 h-4 shrink-0" :style="{ color: 'var(--state-warning)' }" />
        <XCircle v-else-if="toast.type === 'error'" class="w-4 h-4 shrink-0" :style="{ color: 'var(--state-error)' }" />
        <Info v-else class="w-4 h-4 shrink-0" :style="{ color: 'var(--state-info)' }" />
        <span>{{ toast.message }}</span>
        <button class="ml-1 text-muted-foreground hover:text-foreground transition-colors" aria-label="关闭" @click="store.remove(toast.id)">
          <X class="w-3.5 h-3.5" />
        </button>
      </div>
    </TransitionGroup>
  </div>
</template>

<script setup>
import { CheckCircle2, AlertTriangle, XCircle, Info, X } from 'lucide-vue-next'
import { useToastStore } from '../stores/toast'

const store = useToastStore()
</script>
