<template>
  <ModalDialog :title="`追加曲目 · ${name}`" :busy="busy" width="48rem" @close="emit('close')">
    <div class="tab-bar"><button v-for="item in tabs" :key="item.id" class="tab-item" :class="{ active: tab === item.id }" :disabled="busy" @click="tab = item.id">{{ item.title }}</button></div>
    <fieldset :disabled="busy" class="space-y-4 min-w-0">
      <template v-if="tab === 'search'">
        <form class="flex gap-2" @submit.prevent="search"><label class="flex-1 min-w-0"><span class="sr-only">搜索飞牛曲目</span><input v-model="query" class="mm-input" placeholder="在飞牛已入库曲目中搜索" /></label><button class="mm-btn btn-outline" :disabled="searching || !query.trim()"><Search class="w-4 h-4" />搜索</button></form>
        <p v-if="searchTotal != null" class="text-xs text-muted-foreground">共 {{ searchTotal }} 首，展示前 {{ searchTracks.length }} 首；可按艺人或曲名缩小范围。</p>
        <FnosTrackList v-model="selected" :tracks="searchTracks" :loading="searching" selectable title="搜索结果" empty-text="搜索后勾选需要追加的曲目" />
      </template>
      <template v-else-if="tab === 'task'">
        <CompletedTaskPicker v-model="taskId" :disabled="busy" />
      </template>
      <template v-else><label class="mm-field">容器内曲目绝对路径<textarea v-model="paths" class="mm-input h-36 py-2 mono resize-y" placeholder="/singles/艺人/曲名.flac&#10;每行一个文件路径" /></label><p class="text-xs text-muted-foreground">这些文件需已归档到挂载的媒体库，并能通过设置中的路径映射关联到飞牛。等待扫描时可能需要 {{ scanWait }} 秒。</p></template>
    </fieldset>
    <p v-if="error" class="text-sm text-[var(--state-error)] break-words" role="alert">{{ error }}</p>
    <p v-if="busy" class="text-sm text-primary" role="status">正在解析曲目并追加，等待飞牛扫描时可能需要 {{ scanWait }} 秒…</p>
    <div v-if="result" class="rounded-md border border-border p-3 space-y-2 text-sm" role="status"><p class="font-medium" :class="result.status === 'ok' ? 'text-[var(--state-success)]' : 'text-[var(--state-warning)]'">{{ result.status === 'ok' ? '追加完成' : '部分完成' }} · 新增 {{ result.added }} 首 · 已存在 {{ result.already }} 首</p><template v-if="result.unresolved?.length"><p>未匹配 {{ result.unresolved.length }} 个文件；请检查路径映射或飞牛是否已完成扫描。</p><ul class="max-h-40 overflow-auto space-y-1"><li v-for="path in result.unresolved" :key="path" class="mono break-all">{{ path }}</li></ul></template><p v-if="result.error" class="text-[var(--state-error)]">{{ result.error }}</p></div>
    <template #footer><button class="mm-btn btn-outline" :disabled="busy" @click="emit('close')">{{ result ? '完成' : '取消' }}</button><button class="mm-btn btn-primary" :disabled="busy || !canAppend" @click="append"><LoaderCircle v-if="busy" class="w-4 h-4 animate-spin" />{{ busy ? '追加中…' : tab === 'search' ? `追加已选 ${selected.length} 首` : '追加曲目' }}</button></template>
  </ModalDialog>
</template>
<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { LoaderCircle, Search } from 'lucide-vue-next'
import client, { errorMessage } from '../api/client'
import ModalDialog from './ModalDialog.vue'
import FnosTrackList from './FnosTrackList.vue'
import CompletedTaskPicker from './CompletedTaskPicker.vue'
const props = defineProps({ name: String, initialTracks: { type: Array, default: () => [] }, scanWait: { type: Number, default: 120 } })
const emit = defineEmits(['close', 'saved', 'busy'])
const tabs = [{ id: 'search', title: '曲目搜索' }, { id: 'task', title: '下载任务' }, { id: 'paths', title: '容器路径' }]
const tab = ref('search'), query = ref(''), searchTracks = ref(props.initialTracks), selected = ref(props.initialTracks.filter(t => t.guid).map(t => t.guid)), searching = ref(false), searchTotal = ref(null), taskId = ref(''), paths = ref(''), busy = ref(false), error = ref(''), result = ref(null)
let disposed = false, searchVersion = 0
const pathList = computed(() => [...new Set(paths.value.split('\n').map(p => p.trim()).filter(Boolean))])
const canAppend = computed(() => tab.value === 'search' ? selected.value.length > 0 : tab.value === 'task' ? !!taskId.value : pathList.value.length > 0)
watch(tab, () => { error.value = ''; result.value = null })
watch(busy, value => emit('busy', value))
async function search() {
  if (!query.value.trim() || searching.value) return
  const version = ++searchVersion; searching.value = true; error.value = ''
  try { const { data } = await client.get('/fnos/search/tracks', { params: { q: query.value.trim(), limit: 200 } }); if (!disposed && version === searchVersion) { searchTracks.value = data.items; searchTotal.value = data.total; selected.value = [] } }
  catch (e) { if (!disposed && version === searchVersion) error.value = errorMessage(e) }
  finally { if (!disposed && version === searchVersion) searching.value = false }
}
async function append() {
  if (!canAppend.value || busy.value) return
  if (tab.value === 'paths' && pathList.value.some(p => !p.startsWith('/'))) { error.value = '请填写容器内的绝对文件路径（以 / 开头）'; return }
  busy.value = true; error.value = ''; result.value = null
  const body = tab.value === 'search' ? { guids: selected.value } : tab.value === 'task' ? { task_id: taskId.value } : { paths: pathList.value }
  try {
    const { data } = await client.post(`/fnos/playlists/${encodeURIComponent(props.name)}/tracks`, body, { timeout: Math.max(120000, (props.scanWait + 90) * 1000) })
    if (disposed) return
    result.value = data; if (tab.value === 'search') selected.value = []; else if (tab.value === 'task') taskId.value = ''; else paths.value = (data.unresolved || []).join('\n')
    emit('saved', data)
  } catch (e) { if (!disposed) error.value = errorMessage(e, '追加失败，请检查飞牛连接和路径映射。') }
  finally { if (!disposed) busy.value = false }
}
onBeforeUnmount(() => { disposed = true; searchVersion++ })
</script>
