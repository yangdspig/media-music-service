<template>
  <div class="space-y-2">
    <p class="text-xs text-muted-foreground">仅显示当前服务内已完成、指定媒体库且有成功曲目的单曲任务。重启前的任务请改用容器路径。</p>
    <label class="mm-field">{{ optional ? '导入下载任务（可选）' : '选择下载任务' }}<select class="mm-input" :value="modelValue" :disabled="disabled || loading" @change="emit('update:modelValue', $event.target.value)"><option value="">{{ loading ? '正在读取任务…' : optional ? '不导入，创建空歌单' : tasks.length ? '请选择任务' : '没有符合条件的任务' }}</option><option v-for="task in tasks" :key="task.task_id" :value="task.task_id">{{ task.results[0]?.title || task.message || task.task_id.slice(0, 8) }} · {{ task.completed }} 首 · {{ task.library }} · {{ task.task_id.slice(0, 8) }}</option></select></label>
    <p v-if="error" class="text-xs text-[var(--state-error)]" role="alert">{{ error }}</p><button type="button" class="mm-btn btn-ghost" :disabled="disabled || loading" @click="load">刷新任务</button>
  </div>
</template>
<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import client, { errorMessage } from '../api/client'
import { isAlbumTask } from '../utils/format'
const props = defineProps({ modelValue: { type: String, default: '' }, optional: Boolean, disabled: Boolean })
const emit = defineEmits(['update:modelValue'])
const tasks = ref([]), loading = ref(false), error = ref('')
let disposed = false
async function load() {
  if (loading.value) return
  loading.value = true; error.value = ''
  try {
    const { data } = await client.get('/downloads', { params: { limit: 500 } })
    if (disposed) return
    tasks.value = data.filter(t => ['success', 'failed'].includes(t.status) && t.library && t.completed > 0 && !isAlbumTask(t))
    if (props.modelValue && !tasks.value.some(t => t.task_id === props.modelValue)) emit('update:modelValue', '')
  } catch (e) { if (!disposed) error.value = errorMessage(e) }
  finally { if (!disposed) loading.value = false }
}
onMounted(load)
onBeforeUnmount(() => { disposed = true })
</script>
