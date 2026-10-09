<template>
  <label class="mm-field min-w-0" :class="{ 'sm:col-span-2': field.wide }">
    <span class="flex flex-wrap items-center gap-2 text-foreground">{{ field.label }}<span class="mm-badge" :class="mode === 'restart' ? 'badge-warning' : 'badge-success'">{{ mode === 'restart' ? '重启生效' : '即时生效' }}</span><span v-if="disabled" class="text-xs text-muted-foreground">环境变量覆盖</span></span>
    <select v-if="field.type === 'select'" class="mm-input" :value="modelValue" :disabled="disabled" @change="emit('update:modelValue', $event.target.value)"><option v-for="option in field.options" :key="option" :value="option">{{ option }}</option></select>
    <span v-else-if="field.type === 'bool'" class="flex gap-2 items-center min-h-9"><input type="checkbox" class="mm-checkbox" :checked="modelValue" :disabled="disabled" @change="emit('update:modelValue', $event.target.checked)" />{{ modelValue ? '已启用' : '已关闭' }}</span>
    <span v-else-if="field.type === 'secret'" class="flex gap-2">
      <input class="mm-input" :type="visible && modelValue !== MASK ? 'text' : 'password'" :value="modelValue || ''" :disabled="disabled" autocomplete="new-password" :placeholder="modelValue === MASK ? '已配置，输入新值替换' : '未配置'" @input="emit('update:modelValue', $event.target.value || null)" @focus="selectMask" @click="selectMask" />
      <button type="button" class="mm-btn btn-outline shrink-0" :disabled="disabled || modelValue === MASK || !modelValue" :aria-label="visible ? '隐藏输入内容' : '显示输入内容'" @click="visible = !visible"><EyeOff v-if="visible" class="w-4 h-4" /><Eye v-else class="w-4 h-4" /></button>
      <button type="button" class="mm-btn btn-ghost shrink-0" :disabled="disabled || !modelValue" @click="emit('update:modelValue', null)">清空</button>
    </span>
    <input v-else class="mm-input" :type="field.type === 'number' ? 'number' : 'text'" :value="modelValue ?? ''" :disabled="disabled" :min="field.min" :max="field.max" :step="field.step || 1" :placeholder="field.placeholder" @input="input" />
    <span v-if="field.help" class="leading-relaxed">{{ field.help }}</span>
  </label>
</template>
<script setup>
import { ref } from 'vue'
import { Eye, EyeOff } from 'lucide-vue-next'
const MASK = '••••••••'
const props = defineProps({ field: Object, modelValue: [String, Number, Boolean], mode: { type: String, default: 'hot' }, disabled: Boolean })
const emit = defineEmits(['update:modelValue'])
const visible = ref(false)
function selectMask(event) { if (props.modelValue === MASK) event.target.select() }
function input(event) {
  const raw = event.target.value
  emit('update:modelValue', props.field.type === 'number' ? (raw === '' ? null : Number(raw)) : (props.field.nullable && !raw ? null : raw))
}
</script>
