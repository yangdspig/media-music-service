<template>
  <AppShell>
    <div class="mb-5"><h1 class="text-xl font-semibold">专辑管理</h1><p class="text-sm text-muted-foreground mt-1">检索官方曲目表，整张下载、核对匹配结果并归档入库。</p></div>
    <div class="grid gap-5 min-[960px]:grid-cols-[320px_minmax(0,1fr)]">
      <section class="mm-card p-4 self-start space-y-4">
        <h2 class="text-sm font-semibold">专辑检索</h2>
        <form class="space-y-3" @submit.prevent="search">
          <label class="mm-field">专辑关键词<input v-model.trim="keyword" class="mm-input" required placeholder="例如：范特西" /></label>
          <label class="mm-field">艺人（可选）<input v-model.trim="artistKeyword" class="mm-input" placeholder="例如：周杰伦" /></label>
          <button class="mm-btn btn-primary w-full justify-center" :disabled="searching"><Loader2 v-if="searching" class="w-4 h-4 animate-spin" /><Search v-else class="w-4 h-4" />搜索专辑</button>
        </form>
        <p v-if="searched && !results.length && !searching" class="text-sm text-muted-foreground">未找到专辑，试试艺人名或其他关键词。</p>
        <div class="space-y-2 max-h-[640px] overflow-y-auto">
          <button v-for="item in results" :key="item.collection_id" class="w-full flex items-start gap-3 text-left p-3 rounded-md border border-border hover:bg-muted" :class="{ 'border-primary bg-primary/5': album?.collection_id === item.collection_id }" :aria-pressed="album?.collection_id === item.collection_id" @click="router.push(`/albums/${encodeURIComponent(item.collection_id)}`)">
            <img v-if="item.cover_url" :src="item.cover_url" alt="" class="w-14 h-14 object-cover rounded-md shrink-0" loading="lazy" referrerpolicy="no-referrer" /><Disc3 v-else class="w-14 h-14 p-3 bg-muted rounded-md shrink-0 text-primary" />
            <div class="min-w-0"><p class="text-sm font-medium break-words">{{ item.title }}</p><p class="text-xs text-muted-foreground mt-1 break-words">{{ item.artists.join(' / ') }}</p><p class="text-xs text-muted-foreground mt-1">{{ item.release_date?.slice(0, 4) || '年份未知' }} · {{ item.track_count }} 首 · {{ metaLabel(item.meta_source) }}</p></div>
          </button>
        </div>
      </section>
      <div class="min-w-0 space-y-5">
        <p v-if="loading" class="mm-card p-10 text-center text-muted-foreground" role="status">正在读取专辑曲目表…</p>
        <p v-else-if="!album" class="mm-card p-10 text-center text-muted-foreground">搜索并选择一张专辑，查看详情与下载选项。</p>
        <template v-else>
          <section class="mm-card p-5 space-y-4">
            <div class="flex items-start gap-4"><img v-if="album.cover_url" :src="album.cover_url" alt="专辑封面" class="w-24 h-24 sm:w-32 sm:h-32 object-cover rounded-lg shrink-0" referrerpolicy="no-referrer" /><Disc3 v-else class="w-24 h-24 p-5 bg-muted rounded-lg shrink-0 text-primary" /><div class="min-w-0"><h2 class="text-xl font-semibold break-words">{{ album.title }}</h2><p class="text-sm text-muted-foreground mt-2 break-words">{{ album.artists.join(' / ') }}</p><p class="text-xs text-muted-foreground mt-2">{{ album.release_date?.slice(0, 10) || '日期未知' }} · {{ album.genre || '流派未知' }} · {{ album.tracks.length }} 首</p><span class="mm-badge badge-source mt-3">{{ metaLabel(album.meta_source) }}</span></div></div>
            <details v-if="album.description" class="text-sm"><summary class="cursor-pointer text-primary">专辑简介</summary><p class="text-muted-foreground whitespace-pre-line leading-relaxed mt-3">{{ album.description }}</p></details>
          </section>
          <section class="mm-card overflow-hidden">
            <h3 class="text-sm font-semibold p-4 border-b border-border">官方曲目表</h3>
            <div class="hidden min-[960px]:block overflow-x-auto"><table class="mm-table"><thead><tr><th>曲目</th><th>标题</th><th>艺人</th><th>时长</th><th></th></tr></thead><tbody><tr v-for="track in sortedTracks" :key="`${track.disc}-${track.track}`"><td class="mono">{{ track.disc }}-{{ String(track.track).padStart(2, '0') }}</td><td class="font-medium">{{ track.title }}</td><td class="text-muted-foreground">{{ track.artists.join(' / ') }}</td><td class="mono">{{ formatDuration(track.duration_s) }}</td><td><RouterLink class="text-xs text-primary" :to="{ path: '/search', query: { keyword: `${track.artists.join(' ')} ${track.title}`.trim() } }">单曲搜索</RouterLink></td></tr></tbody></table></div>
            <ul class="min-[960px]:hidden divide-y divide-border"><li v-for="track in sortedTracks" :key="`${track.disc}-${track.track}`" class="p-4"><p class="text-sm break-words"><span class="mono text-muted-foreground">{{ track.disc }}-{{ String(track.track).padStart(2, '0') }}</span> {{ track.title }}</p><div class="flex flex-wrap justify-between gap-2 mt-2 text-xs text-muted-foreground"><span>{{ track.artists.join(' / ') }} · {{ formatDuration(track.duration_s) }}</span><RouterLink class="text-primary" :to="{ path: '/search', query: { keyword: `${track.artists.join(' ')} ${track.title}`.trim() } }">单曲搜索</RouterLink></div></li></ul>
          </section>
          <section class="mm-card p-5 space-y-4">
            <h3 class="text-sm font-semibold">操作选项</h3>
            <SourcePicker v-model="form.sources" :sources="sources" />
            <div class="grid sm:grid-cols-2 gap-4"><label class="mm-field">保存子目录<input v-model.trim="form.subdir" class="mm-input" placeholder="留空按艺人与专辑自动命名" /></label><label class="mm-field">单首上限（MB）<input v-model.number="form.maxSize" class="mm-input" type="number" min="0" step="any" placeholder="留空或 0 使用服务配置" /></label><label class="mm-field">专辑显示名覆盖<input v-model.trim="form.album_title" class="mm-input" placeholder="可修正罗马音显示名" /></label><label class="mm-field">艺人显示名覆盖<input v-model.trim="form.artist" class="mm-input" placeholder="留空沿用元数据" /></label></div>
            <p class="text-xs text-muted-foreground">整辑下载先保存到下载目录，完成后选择媒体库归档；无合格小体积候选时服务可能放宽上限。</p>
            <div class="flex flex-wrap gap-2 border-t border-border pt-4"><button class="mm-btn btn-primary" :disabled="submitting || !album.tracks.length || ['pending', 'running'].includes(task?.status)" @click="downloadAlbum"><Loader2 v-if="submitting" class="w-4 h-4 animate-spin" /><Download v-else class="w-4 h-4" />下载专辑</button><button class="mm-btn btn-ghost" :disabled="!manifest || !task?.completed" @click="archiveOpen = true"><Archive class="w-4 h-4" />归档入库</button></div>
          </section>
          <section v-if="task" class="mm-card p-4 space-y-3"><div class="flex flex-wrap justify-between gap-2"><span class="text-sm font-medium">{{ TASK_STATUS_TEXT[task.status] || task.status }} · {{ task.completed }} 成功 / {{ task.failed }} 失败</span><RouterLink class="text-xs text-primary" :to="{ path: '/tasks', query: { task: task.task_id } }">查看任务详情</RouterLink></div><div class="h-2 bg-muted rounded-full overflow-hidden"><div class="h-full bg-primary" :style="{ width: `${task.total ? (task.completed + task.failed) / task.total * 100 : 0}%` }"></div></div><p class="text-xs text-muted-foreground break-words">{{ task.current || task.message }}</p></section>
          <section v-if="manifest" class="mm-card p-5"><ManifestSummary :manifest="manifest" /></section>
        </template>
      </div>
    </div>
    <ArchiveDialog v-if="archiveOpen && task" :task-id="task.task_id" @close="archiveOpen = false" />
  </AppShell>
</template>
<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Archive, Disc3, Download, Loader2, Search } from 'lucide-vue-next'
import AppShell from '../components/AppShell.vue'
import SourcePicker from '../components/SourcePicker.vue'
import ArchiveDialog from '../components/ArchiveDialog.vue'
import ManifestSummary from '../components/ManifestSummary.vue'
import client, { errorMessage } from '../api/client'
import { useToastStore } from '../stores/toast'
import { formatDuration, TASK_STATUS_TEXT } from '../utils/format'
const route = useRoute(), router = useRouter(), toast = useToastStore()
const keyword = ref(''), artistKeyword = ref(''), results = ref([]), searched = ref(false), searching = ref(false), album = ref(null), loading = ref(false), sources = ref([]), submitting = ref(false), task = ref(null), manifest = ref(null), archiveOpen = ref(false)
const form = reactive({ sources: [], subdir: '', maxSize: '', album_title: '', artist: '' })
const sortedTracks = computed(() => [...(album.value?.tracks || [])].sort((a, b) => a.disc - b.disc || a.track - b.track))
const lifecycle = new AbortController()
let detailController, taskController, pollTimer
function metaLabel(value) { return { itunes: 'iTunes', netease: '网易云', qq: 'QQ', 'itunes+netease': 'iTunes + 网易云', 'itunes+qq': 'iTunes + QQ' }[value] || value }
async function search() {
  if (searching.value || !keyword.value) return
  searching.value = true; searched.value = true
  try { results.value = (await client.get('/albums/search', { params: { keyword: keyword.value, artist: artistKeyword.value || undefined, limit: 20 }, signal: lifecycle.signal })).data }
  catch (e) { if (!lifecycle.signal.aborted) { results.value = []; toast.show(errorMessage(e, '专辑搜索失败'), 'error') } }
  finally { searching.value = false }
}
async function loadAlbum(id) {
  detailController?.abort()
  const controller = detailController = new AbortController()
  album.value = null; loading.value = !!id
  form.album_title = ''; form.artist = ''; form.subdir = ''
  if (!id) return
  try { album.value = (await client.get(`/albums/${encodeURIComponent(id)}`, { signal: controller.signal })).data }
  catch (e) { if (!controller.signal.aborted) toast.show(errorMessage(e, '专辑详情读取失败'), 'error') }
  finally { if (!controller.signal.aborted) loading.value = false }
}
async function downloadAlbum() {
  if (submitting.value) return
  if (form.maxSize !== '' && (!Number.isFinite(Number(form.maxSize)) || form.maxSize < 0)) { toast.show('大小上限须为非负数', 'warning'); return }
  submitting.value = true
  const id = album.value.collection_id
  try {
    const body = {}
    if (form.sources.length) body.sources = form.sources
    for (const key of ['subdir', 'album_title', 'artist']) if (form[key]) body[key] = form[key]
    if (form.maxSize > 0) body.max_size_mb = Number(form.maxSize)
    const { data } = await client.post(`/albums/${encodeURIComponent(id)}/download`, body, { signal: lifecycle.signal })
    toast.show('专辑下载已提交，完成后可查看匹配清单并归档', 'success')
    await router.replace({ path: `/albums/${encodeURIComponent(id)}`, query: { task: data.task_id } })
  } catch (e) { if (!lifecycle.signal.aborted) toast.show(errorMessage(e, '专辑下载提交失败'), 'error') }
  finally { submitting.value = false }
}
function watchTask(id) {
  clearTimeout(pollTimer); taskController?.abort(); task.value = null; manifest.value = null
  if (!id) return
  const controller = taskController = new AbortController()
  async function poll() {
    try {
      const data = (await client.get(`/downloads/${encodeURIComponent(id)}`, { signal: controller.signal })).data
      task.value = data
      if (['pending', 'running'].includes(data.status)) pollTimer = setTimeout(poll, 3000)
      else if (data.manifest_path) manifest.value = (await client.get(`/downloads/${encodeURIComponent(id)}/manifest`, { signal: controller.signal })).data
    } catch (e) { if (!controller.signal.aborted) toast.show(errorMessage(e, '任务或匹配清单读取失败'), 'error') }
  }
  poll()
}
watch(() => route.params.collectionId, loadAlbum, { immediate: true })
watch(() => route.query.task, watchTask, { immediate: true })
onMounted(async () => { try { sources.value = (await client.get('/sources', { signal: lifecycle.signal })).data.filter(s => s.available && s.supports_search && s.supports_download) } catch (e) { if (!lifecycle.signal.aborted) toast.show(errorMessage(e, '获取音源失败'), 'error') } })
onBeforeUnmount(() => { lifecycle.abort(); detailController?.abort(); taskController?.abort(); clearTimeout(pollTimer) })
</script>
