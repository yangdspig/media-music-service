<template>
  <AppShell>
    <div class="flex flex-wrap items-center justify-between gap-3 mb-5"><div><h1 class="text-xl font-semibold">任务中心</h1><p class="text-sm text-muted-foreground mt-1">查看进度、匹配明细与历史，完成后归档入库。</p></div><button class="mm-btn btn-primary" :disabled="refreshing" @click="refresh(false)"><RefreshCw class="w-4 h-4" :class="{ 'animate-spin': refreshing }" />刷新</button></div>
    <div class="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-5"><div v-for="stat in stats" :key="stat.label" class="mm-card p-4"><p class="text-xs text-muted-foreground">{{ stat.label }}</p><p class="text-xl font-semibold mt-2">{{ stat.count }}</p></div></div>
    <section class="mm-card overflow-hidden">
      <div class="px-4 tab-bar" role="tablist" aria-label="任务列表"><button class="tab-item" role="tab" :class="{ active: tab === 'memory' }" :aria-selected="tab === 'memory'" @click="tab = 'memory'">本次服务任务</button><button class="tab-item" role="tab" :class="{ active: tab === 'history' }" :aria-selected="tab === 'history'" @click="tab = 'history'">历史记录</button></div>
      <p class="px-4 py-3 text-xs text-muted-foreground">{{ tab === 'memory' ? '任务每 3 秒刷新；服务重启后内存任务会丢失。' : '历史来自 SQLite；重启前的专辑清单可通过下载目录中的 manifest.json 归档。' }}</p>
      <p v-if="!rows.length" class="p-8 text-center text-sm text-muted-foreground">{{ refreshing ? '加载中…' : '暂无任务，去搜索一首歌试试。' }}</p>
      <template v-else>
        <div class="hidden min-[960px]:block overflow-x-auto"><table class="mm-table" aria-label="任务列表"><thead><tr><th>任务 / 名称</th><th>状态</th><th>进度</th><th>当前曲目或阶段</th><th>保存目录</th><th>操作</th></tr></thead><tbody><tr v-for="row in rows" :key="row.task_id"><td><p class="font-medium">{{ taskDisplayName(row) }}</p><p class="mono text-muted-foreground mt-1">{{ row.task_id }}</p></td><td><span class="mm-badge" :class="`badge-${row.status}`">{{ TASK_STATUS_TEXT[row.status] || row.status }}</span></td><td><div class="progress-track"><div class="progress-fill" :style="{ width: `${progress(row)}%` }"></div></div><p class="mono text-muted-foreground mt-1">{{ row.completed || 0 }} 成功 / {{ row.failed || 0 }} 失败 / {{ row.total }} 总计</p></td><td class="text-xs text-muted-foreground">{{ row.current || (row.updated_at ? new Date(row.updated_at * 1000).toLocaleString() : '—') }}</td><td class="mono text-muted-foreground">{{ row.save_dir || '—' }}</td><td><div class="flex flex-wrap gap-2"><button class="mm-btn btn-ghost" @click="openDetails(row)">详情</button><button v-if="canArchive(row)" class="mm-btn btn-ghost" @click="archiveId = row.task_id">去归档</button><button v-if="['pending', 'running'].includes(row.status) && tab === 'memory'" class="mm-btn btn-danger-ghost" :disabled="row.status !== 'pending' || cancelingId === row.task_id" :title="row.status === 'running' ? '运行中任务不可中断' : ''" @click="cancel(row)">取消</button></div></td></tr></tbody></table></div>
        <div class="min-[960px]:hidden divide-y divide-border"><article v-for="row in rows" :key="row.task_id" class="p-4 space-y-3"><div class="flex items-start justify-between gap-2"><h2 class="text-sm font-medium break-words min-w-0">{{ taskDisplayName(row) }}</h2><span class="mm-badge shrink-0" :class="`badge-${row.status}`">{{ TASK_STATUS_TEXT[row.status] || row.status }}</span></div><p class="mono text-muted-foreground">{{ row.task_id }}</p><div class="h-1.5 rounded-full bg-muted overflow-hidden"><div class="h-full bg-primary" :style="{ width: `${progress(row)}%` }"></div></div><p class="text-xs text-muted-foreground">{{ row.completed || 0 }} 成功 / {{ row.failed || 0 }} 失败 / {{ row.total }} 总计</p><p class="text-xs text-muted-foreground break-all">{{ row.current || row.save_dir || row.message }}</p><div class="flex flex-wrap gap-2"><button class="mm-btn btn-ghost" @click="openDetails(row)">详情</button><button v-if="canArchive(row)" class="mm-btn btn-ghost" @click="archiveId = row.task_id">去归档</button><button v-if="['pending', 'running'].includes(row.status) && tab === 'memory'" class="mm-btn btn-danger-ghost" :disabled="row.status !== 'pending' || cancelingId === row.task_id" :title="row.status === 'running' ? '运行中任务不可中断' : ''" @click="cancel(row)">取消</button></div></article></div>
      </template>
    </section>
    <ModalDialog v-if="detailTask" title="任务详情" width="56rem" @close="selectedTask = null">
      <div class="space-y-2"><p class="text-sm font-medium break-words">{{ taskDisplayName(detailTask) }}</p><p class="mono text-muted-foreground">{{ detailTask.task_id }} · {{ TASK_STATUS_TEXT[detailTask.status] }}</p><p class="text-xs break-words">{{ detailTask.message }}</p><p class="mono text-muted-foreground break-all">{{ detailTask.save_dir }}</p></div>
      <p v-for="(error, index) in detailTask.errors" :key="index" class="text-xs text-[var(--state-error)] break-words">{{ error }}</p>
      <p v-if="manifestLoading" class="text-sm text-muted-foreground" role="status">读取匹配清单…</p>
      <ManifestSummary v-else-if="manifest" :manifest="manifest" />
      <ul v-else class="divide-y divide-border"><li v-for="(item, index) in detailTask.results" :key="index" class="py-3 text-xs flex justify-between items-start gap-3"><div class="min-w-0"><p class="font-medium break-words">{{ item.title || item.file }}</p><p class="mono text-muted-foreground break-all mt-1">{{ item.file || item.save_path }}</p></div><span class="text-muted-foreground whitespace-nowrap">大小 {{ formatSize(item.size_bytes) }}</span></li></ul>
      <template #footer><RouterLink v-if="manifest?.album?.collection_id" class="mm-btn btn-ghost" :to="{ path: `/albums/${encodeURIComponent(manifest.album.collection_id)}`, query: { task: detailTask.task_id } }">专辑详情</RouterLink><button v-if="canArchive(detailTask)" class="mm-btn btn-primary" @click="archiveId = detailTask.task_id; selectedTask = null"><Archive class="w-4 h-4" />归档入库</button><button class="mm-btn btn-ghost" @click="selectedTask = null">关闭</button></template>
    </ModalDialog>
    <ArchiveDialog v-if="archiveId" :task-id="archiveId" @close="archiveId = ''" />
  </AppShell>
</template>
<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { Archive, RefreshCw } from 'lucide-vue-next'
import AppShell from '../components/AppShell.vue'
import ModalDialog from '../components/ModalDialog.vue'
import ArchiveDialog from '../components/ArchiveDialog.vue'
import ManifestSummary from '../components/ManifestSummary.vue'
import client, { errorMessage } from '../api/client'
import { useToastStore } from '../stores/toast'
import { formatSize, taskDisplayName, TASK_STATUS_TEXT } from '../utils/format'
const route = useRoute(), toast = useToastStore()
const tasks = ref([]), history = ref([]), refreshing = ref(false), tab = ref('memory'), cancelingId = ref(''), archiveId = ref(''), selectedTask = ref(null), manifest = ref(null), manifestLoading = ref(false)
const rows = computed(() => tab.value === 'memory' ? tasks.value : history.value)
const detailTask = computed(() => selectedTask.value ? tasks.value.find(t => t.task_id === selectedTask.value.task_id) || selectedTask.value : null)
const stats = computed(() => [
  { label: '进行中', count: tasks.value.filter(t => t.status === 'running').length },
  { label: '等待中', count: tasks.value.filter(t => t.status === 'pending').length },
  { label: '今日成功', count: history.value.filter(t => t.status === 'success' && isToday(t.updated_at)).length },
  { label: '今日失败', count: history.value.filter(t => t.status === 'failed' && isToday(t.updated_at)).length },
])
const lifecycle = new AbortController()
let timer, manifestController
function isToday(timestamp) { return timestamp && new Date(timestamp * 1000).toDateString() === new Date().toDateString() }
function progress(row) { return row.total ? Math.min(100, (Number(row.completed || 0) + Number(row.failed || 0)) / row.total * 100) : 0 }
function canArchive(row) { const task = tasks.value.find(t => t.task_id === row.task_id) || row; return !!task.manifest_path && task.completed > 0 && !['pending', 'running'].includes(task.status) }
async function refresh(silent = false) {
  if (refreshing.value) return
  refreshing.value = true
  const result = await Promise.allSettled([client.get('/downloads', { params: { limit: 100 }, signal: lifecycle.signal }), client.get('/history', { params: { limit: 100 }, signal: lifecycle.signal })])
  if (!lifecycle.signal.aborted) {
    if (result[0].status === 'fulfilled') tasks.value = result[0].value.data
    else if (!silent) toast.show(errorMessage(result[0].reason, '任务加载失败'), 'error')
    if (result[1].status === 'fulfilled') history.value = result[1].value.data
    else if (!silent) toast.show(errorMessage(result[1].reason, '历史加载失败'), 'error')
  }
  refreshing.value = false
}
async function cancel(row) {
  if (row.status !== 'pending' || cancelingId.value) return
  cancelingId.value = row.task_id
  try { const { data } = await client.post(`/downloads/${encodeURIComponent(row.task_id)}/cancel`); toast.show(data.canceled ? '任务已取消' : '任务已开始或结束，无法取消', data.canceled ? 'success' : 'warning'); await refresh(true) }
  catch (e) { toast.show(errorMessage(e, '取消失败'), 'error') }
  finally { cancelingId.value = '' }
}
function openDetails(row) { selectedTask.value = row }
watch(() => [detailTask.value?.task_id, detailTask.value?.manifest_path], async ([id, path], previous) => {
  if (previous && previous[0] === id && previous[1] === path) return
  manifestController?.abort(); manifest.value = null; manifestLoading.value = false
  if (!id || !path) return
  const controller = manifestController = new AbortController()
  manifestLoading.value = true
  try { manifest.value = (await client.get(`/downloads/${encodeURIComponent(id)}/manifest`, { signal: controller.signal })).data }
  catch (e) { if (!controller.signal.aborted) toast.show(errorMessage(e, '匹配清单读取失败'), 'error') }
  finally { if (!controller.signal.aborted) manifestLoading.value = false }
})
async function openRequestedTask(id) {
  if (typeof id !== 'string' || !id) return
  const cached = tasks.value.find(t => t.task_id === id) || history.value.find(t => t.task_id === id)
  if (cached) { selectedTask.value = cached; return }
  try { selectedTask.value = (await client.get(`/downloads/${encodeURIComponent(id)}`, { signal: lifecycle.signal })).data }
  catch (e) { if (!lifecycle.signal.aborted) toast.show(errorMessage(e, '任务读取失败'), 'error') }
}
watch(() => route.query.task, openRequestedTask)
onMounted(async () => {
  await refresh(); if (lifecycle.signal.aborted) return
  openRequestedTask(route.query.task)
  async function poll() { await refresh(true); if (!lifecycle.signal.aborted) timer = setTimeout(poll, 3000) }
  timer = setTimeout(poll, 3000)
})
onBeforeUnmount(() => { clearTimeout(timer); lifecycle.abort(); manifestController?.abort() })
</script>
