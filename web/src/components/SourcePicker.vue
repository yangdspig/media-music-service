<template>
  <div class="flex flex-wrap gap-2" role="group" aria-label="下载音源">
    <button v-for="source in sources" :key="source.name" type="button" class="source-chip px-3 py-1.5 rounded-full border border-border text-xs" :data-active="modelValue.includes(source.name)" :aria-pressed="modelValue.includes(source.name)" :disabled="disabled" @click="toggle(source.name)">{{ sourceDisplayName(source.name) }}</button>
    <span v-if="!modelValue.length" class="text-xs text-muted-foreground self-center">使用服务默认音源</span>
  </div>
</template>
<script setup>
import { sourceDisplayName } from '../utils/format'
const props = defineProps({ sources: { type: Array, default: () => [] }, modelValue: { type: Array, default: () => [] }, disabled: Boolean })
const emit = defineEmits(['update:modelValue'])
function toggle(name) { emit('update:modelValue', props.modelValue.includes(name) ? props.modelValue.filter(n => n !== name) : [...props.modelValue, name]) }
</script>
