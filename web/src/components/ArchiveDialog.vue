<template>
  <ModalDialog title="归档专辑" :busy="busy" @close="emit('close')">
    <template v-if="!result">
      <p class="text-xs text-muted-foreground break-all">任务 {{ taskId }}</p>
      <label class="mm-field">目标媒体库<select v-model="form.library" class="mm-input"><option v-for="lib in libraries" :key="lib.name" :value="lib.name">{{ lib.name }}{{ lib.default ? '（默认库）' : '' }}</option></select></label>
      <label class="mm-field">专辑显示名覆盖<input v-model.trim="form.album_title" class="mm-input" placeholder="留空沿用匹配清单" /></label>
      <label class="mm-field">艺人显示名覆盖<input v-model.trim="form.artist" class="mm-input" placeholder="留空沿用匹配清单" /></label>
      <label class="mm-field">合集归档<select v-model="form.compilation" class="mm-input"><option value="auto">自动判定</option><option value="true">强制合集，归入群星目录</option><option value="false">强制普通专辑</option></select></label>
      <label class="flex items-center gap-2 text-sm"><input v-model="form.overwrite" type="checkbox" class="mm-checkbox" />覆盖已存在文件</label>
      <p v-if="!libraries.length" class="text-sm text-muted-foreground">暂无可用媒体库，请先配置库根目录。</p>
    </template>
    <template v-else>
      <p class="flex gap-2 items-center text-sm">归档结果 <span class="mm-badge" :class="result.status === 'success' ? 'badge-success' : 'badge-failed'">{{ result.status === 'success' ? '成功' : result.status === 'partial' ? '部分成功' : '失败' }}</span></p>
      <p class="mono break-all">{{ result.library_dir }}</p>
      <ul class="divide-y divide-border"><li v-for="(track, index) in result.tracks" :key="index" class="py-3"><div class="flex flex-wrap justify-between gap-2 text-sm"><span>{{ track.disc }}-{{ track.track }} {{ track.title }}</span><span>{{ actionText(track.action) }} · {{ track.lyric === 'ok' ? '歌词已入库' : track.lyric === 'missing' ? '歌词缺失' : '—' }}</span></div><p v-if="track.error" class="text-xs text-[var(--state-error)] break-words mt-1">{{ track.error }}</p><RouterLink v-if="track.action === 'failed'" class="text-xs text-primary" :to="{ path: '/search', query: { keyword: `${form.artist} ${track.title}`.trim() } }">重搜此曲</RouterLink></li></ul>
      <p v-for="(error, index) in result.errors" :key="index" class="text-sm text-[var(--state-error)] break-words">{{ error }}</p>
    </template>
    <template #footer><button class="mm-btn btn-ghost" :disabled="busy" @click="emit('close')">关闭</button><button v-if="!result" class="mm-btn btn-primary" :disabled="busy || !libraries.length" @click="submit"><Loader2 v-if="busy" class="w-4 h-4 animate-spin" />开始归档</button></template>
  </ModalDialog>
</template>
<script setup>
import { onMounted, reactive, ref } from 'vue'
import { Loader2 } from 'lucide-vue-next'
import ModalDialog from './ModalDialog.vue'
import client, { errorMessage } from '../api/client'
import { useToastStore } from '../stores/toast'
const props = defineProps({ taskId: { type: String, required: true } })
const emit = defineEmits(['close', 'archived'])
const toast = useToastStore()
const busy = ref(false), result = ref(null), libraries = ref([])
const form = reactive({ library: '', album_title: '', artist: '', compilation: 'auto', overwrite: false })
onMounted(async () => { try { libraries.value = (await client.get('/libraries')).data; form.library = (libraries.value.find(l => l.default) || libraries.value[0])?.name || '' } catch (e) { toast.show(errorMessage(e, '获取媒体库失败'), 'error') } })
function actionText(action) { return { linked: '已链接', copied: '已复制', skipped: '已跳过', failed: '失败', tag_unsupported: '不支持标签' }[action] || action }
async function submit() {
  if (busy.value) return
  busy.value = true
  try {
    const body = { task_id: props.taskId, library: form.library, overwrite: form.overwrite }
    if (form.album_title) body.album_title = form.album_title
    if (form.artist) body.artist = form.artist
    if (form.compilation !== 'auto') body.compilation = form.compilation === 'true'
    result.value = (await client.post('/albums/archive', body, { timeout: 600000 })).data
    toast.show(result.value.status === 'success' ? '归档完成' : '归档存在失败项，请查看明细', result.value.status === 'success' ? 'success' : 'warning')
    emit('archived', result.value)
  } catch (e) { toast.show(errorMessage(e, '归档失败'), 'error') }
  finally { busy.value = false }
}
</script>
