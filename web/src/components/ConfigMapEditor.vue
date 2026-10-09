<template>
  <label class="mm-field min-w-0"><span class="text-foreground">{{ label }}</span>
    <textarea v-model="raw" class="mm-input mono h-32 py-2 resize-y" spellcheck="false" :disabled="disabled" @input="parse" />
    <span v-if="invalid" class="text-[var(--state-error)]">{{ stringValues ? '请填写 JSON 对象，键和值均为非空文本。' : '请填写有效的 JSON 对象。' }}</span>
    <span v-else-if="help" class="leading-relaxed">{{ help }}</span>
  </label>
</template>
<script setup>
import { onBeforeUnmount, ref, watch } from 'vue'
const props = defineProps({ modelValue: Object, label: String, help: String, stringValues: Boolean, disabled: Boolean })
const emit = defineEmits(['update:modelValue', 'invalid'])
const raw = ref(JSON.stringify(props.modelValue || {}, null, 2)), invalid = ref(false)
watch(() => props.modelValue, (value) => {
  if (!invalid.value && JSON.stringify(value || {}) !== JSON.stringify(read())) raw.value = JSON.stringify(value || {}, null, 2)
})
function read() { try { return JSON.parse(raw.value) } catch { return null } }
function parse() {
  const value = read()
  invalid.value = !value || typeof value !== 'object' || Array.isArray(value) || (props.stringValues && Object.entries(value).some(([k, v]) => !k.trim() || typeof v !== 'string' || !v.trim()))
  emit('invalid', invalid.value)
  if (!invalid.value) emit('update:modelValue', value)
}
onBeforeUnmount(() => emit('invalid', false))
</script>
