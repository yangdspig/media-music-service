<template>
  <ModalDialog title="提交下载任务" :busy="submitting" @close="emit('close')">
    <p class="text-sm text-muted-foreground">已选 {{ tracks.length }} 首，提交后进入任务中心查看进度。</p>
    <label class="mm-field">目标媒体库<select v-model="form.library" class="mm-input"><option value="">仅下载，不归档</option><option v-for="lib in libraries" :key="lib.name" :value="lib.name">{{ lib.name }}{{ lib.default ? '（默认库）' : '' }}</option></select></label>
    <label class="mm-field">保存子目录<input v-model.trim="form.subdir" class="mm-input" placeholder="留空自动组织目录" /></label>
    <label class="mm-field">单文件上限（MB）<input v-model.number="form.maxSize" type="number" min="0" step="any" class="mm-input" placeholder="留空或 0 使用服务配置" /></label>
    <label class="mm-field">飞牛歌单名<input v-model.trim="form.playlist" class="mm-input" placeholder="选媒体库后可填写；需配置飞牛连接" :disabled="!form.library" /></label>
    <template #footer><button class="mm-btn btn-ghost" :disabled="submitting" @click="emit('close')">取消</button><button class="mm-btn btn-primary" :disabled="submitting || !tracks.length" @click="submit"><Loader2 v-if="submitting" class="w-4 h-4 animate-spin" />确认提交</button></template>
  </ModalDialog>
</template>
<script setup>
import { onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Loader2 } from 'lucide-vue-next'
import ModalDialog from './ModalDialog.vue'
import client, { errorMessage } from '../api/client'
import { useToastStore } from '../stores/toast'
const props = defineProps({ tracks: { type: Array, required: true } })
const emit = defineEmits(['close'])
const toast = useToastStore(), router = useRouter()
const libraries = ref([]), submitting = ref(false)
const form = reactive({ library: '', subdir: '', maxSize: '', playlist: '' })
watch(() => form.library, () => { form.playlist = '' })
onMounted(async () => { try { libraries.value = (await client.get('/libraries')).data } catch (e) { toast.show(errorMessage(e, '媒体库列表加载失败，可选择仅下载'), 'error') } })
async function submit() {
  if (submitting.value) return
  if (form.maxSize !== '' && (!Number.isFinite(Number(form.maxSize)) || Number(form.maxSize) < 0)) { toast.show('大小上限须为非负数', 'warning'); return }
  submitting.value = true
  try {
    const body = { tracks: props.tracks }
    if (form.library) body.library = form.library
    if (form.subdir) body.subdir = form.subdir
    if (form.maxSize > 0) body.max_size_mb = Number(form.maxSize)
    if (form.playlist && form.library) body.playlist = form.playlist
    const { data } = await client.post('/downloads', body)
    const skipped = props.tracks.length - data.total
    toast.show(`下载任务已提交${skipped > 0 ? `，已跳过 ${skipped} 首超限曲目` : ''}`, 'success')
    emit('close')
    await router.push({ path: '/tasks', query: { task: data.task_id } })
  } catch (e) { toast.show(errorMessage(e, '下载提交失败'), 'error') }
  finally { submitting.value = false }
}
</script>
