<template>
  <div>
    <button type="button" class="mobile-disclosure-toggle" :aria-expanded="open" :aria-controls="id" @click="open = !open">
      <span class="min-w-0 text-left"><span class="block text-sm font-medium">{{ label }}</span><span class="mt-1 block text-xs text-muted-foreground break-words">{{ summary }}</span></span>
      <span class="flex shrink-0 items-center gap-1 text-xs text-primary">{{ open ? '收起' : '展开' }}<ChevronDown class="h-4 w-4 transition-transform" :class="{ 'rotate-180': open }" aria-hidden="true" /></span>
    </button>
    <div :id="id" class="mobile-disclosure-content" :class="{ 'is-open': open }"><slot /></div>
  </div>
</template>
<script setup>
import { ref } from 'vue'
import { ChevronDown } from 'lucide-vue-next'
defineProps({ id: { type: String, required: true }, label: String, summary: String })
const open = ref(false)
</script>
