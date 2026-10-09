<template>
  <AppShell>
    <div class="flex flex-wrap items-center justify-between gap-3 mb-5"><div><h1 class="text-xl font-semibold">媒体库</h1><p class="text-sm text-muted-foreground mt-1">浏览库内目录，替换曲目、清理、迁移单曲和回填歌词。</p></div><label class="mm-field">当前媒体库<select v-model="library" class="mm-input min-w-40" :disabled="anyBusy"><option v-for="lib in libraries" :key="lib.name" :value="lib.name">{{ lib.name }}{{ lib.default ? '（默认库）' : '' }}</option></select></label></div>
    <p v-if="!loadingLibraries && !libraries.length" class="mm-card p-8 text-sm text-muted-foreground">暂无可用媒体库，请先配置 library_root 或命名库根。</p>
    <div v-else class="grid gap-5 min-[960px]:grid-cols-[300px_minmax(0,1fr)]">
      <aside class="mm-card p-4 self-start space-y-3 min-w-0">
        <div class="flex items-center justify-between"><h2 class="text-sm font-semibold">库结构</h2><button class="mm-btn btn-ghost" :disabled="browsing || anyBusy" @click="loadDirectory(path)"><RefreshCw class="w-4 h-4" :class="{ 'animate-spin': browsing }" />刷新</button></div>
        <p class="mono text-muted-foreground break-all">{{ libraries.find(lib => lib.name === library)?.root }}</p>
        <div class="flex items-center gap-2"><button class="text-xs text-primary" :disabled="!path || browsing || anyBusy" @click="loadDirectory(path.split('/').slice(0, -1).join('/'))"><ArrowLeft class="inline w-3.5 h-3.5" />上一级</button><button class="text-xs text-primary" :disabled="!path || browsing || anyBusy" @click="loadDirectory('')">库根</button></div>
        <p class="text-xs text-muted-foreground break-all">{{ path || '根目录' }}</p>
        <p v-if="browsing && !entries.length" class="text-sm text-muted-foreground" role="status">正在读取目录…</p>
        <p v-else-if="!entries.length" class="text-sm text-muted-foreground">{{ browseError || '目录为空' }}</p>
        <div class="max-h-[600px] overflow-y-auto space-y-1"><button v-for="entry in entries" :key="entry.path" class="w-full text-left flex gap-2 items-start p-2 rounded-md hover:bg-muted min-w-0" :disabled="anyBusy || (entry.type !== 'directory' && !entry.audio)" @click="pickEntry(entry)"><Folder v-if="entry.type === 'directory'" class="w-4 h-4 text-primary shrink-0 mt-0.5" /><Music2 v-else-if="entry.audio" class="w-4 h-4 shrink-0 mt-0.5" /><File v-else class="w-4 h-4 shrink-0 mt-0.5 text-muted-foreground" /><span class="min-w-0"><span class="text-sm break-words">{{ entry.name }}</span><span v-if="entry.size_bytes != null" class="block text-xs text-muted-foreground">{{ formatSize(entry.size_bytes) }}</span></span></button></div>
        <button v-if="hasMore" class="mm-btn btn-ghost w-full justify-center" :disabled="browsing || anyBusy" @click="loadDirectory(path, true)">加载更多目录项</button>
        <p class="text-xs text-muted-foreground">点击音频文件可填写右侧操作范围。</p>
      </aside>
      <div class="min-w-0 space-y-4">
        <div class="grid gap-4 lg:grid-cols-2">
          <section v-for="operation in operations" :key="operation.id" class="mm-card p-4 min-w-0 space-y-4">
            <div><h2 class="font-semibold text-sm flex items-center gap-2"><component :is="operation.icon" class="w-4 h-4 text-primary" />{{ operation.title }}</h2><p class="text-xs text-muted-foreground mt-2 leading-relaxed">{{ operation.description }}</p></div>
            <div class="grid grid-cols-2 gap-3">
              <label v-for="field in operation.fields" :key="field.key" class="mm-field" :class="{ 'col-span-2': field.wide }">{{ field.label }}
                <select v-if="field.type === 'library'" v-model="forms[operation.id][field.key]" class="mm-input" :disabled="anyBusy"><option v-for="lib in libraries" :key="lib.name" :value="lib.name">{{ lib.name }}</option></select>
                <input v-else v-model="forms[operation.id][field.key]" class="mm-input" :type="field.type || 'text'" :min="field.key === 'limit' ? 1 : 0" :step="field.key === 'max_size_mb' ? 'any' : 1" :placeholder="field.placeholder" :disabled="anyBusy" />
              </label>
            </div>
            <SourcePicker v-if="['replace_track', 'backfill_lyrics'].includes(operation.id)" v-model="forms[operation.id].sources" :sources="sources" :disabled="anyBusy" />
            <label v-if="operation.id === 'replace_track'" class="flex items-start gap-2 text-xs"><input v-model="forms.replace_track.force" type="checkbox" class="mm-checkbox shrink-0" :disabled="anyBusy" />新候选音质不高于现有版本时仍强制替换</label>
            <div class="flex flex-wrap gap-2"><button v-if="operation.preview" class="mm-btn btn-ghost" :disabled="anyBusy" @click="preview(operation)"><Loader2 v-if="busy === operation.id" class="w-4 h-4 animate-spin" />预览结果</button><button class="mm-btn" :class="operation.id === 'cleanup' ? 'btn-danger-ghost' : 'btn-primary'" :disabled="anyBusy || (operation.preview && !previews[operation.id])" @click="openConfirm(operation)">{{ operation.preview ? '确认执行' : '替换曲目' }}</button></div>
            <OperationResult v-if="results[operation.id]" :result="results[operation.id]" />
          </section>
        </div>
        <p class="mm-notice">清理、迁移与歌词回填须先预览。修改操作范围后需重新预览；替换曲目接口会直接搜索并替换，提交前请核对目标。</p>
        <p class="text-xs text-muted-foreground">歌词回填只写同名 .lrc，不覆盖已有歌词。飞牛已入库曲目需按部署文档触发重新刮削。</p>
      </div>
    </div>
    <ModalDialog v-if="confirmation" :title="`确认${confirmation.operation.title}`" :busy="!!busy" @close="confirmation = null">
      <p class="text-sm">{{ confirmation.operation.id === 'cleanup' ? '以下范围内的文件或目录将被删除。' : confirmation.operation.id === 'migrate_singles' ? '以下范围内的单曲专辑将被迁移。' : confirmation.operation.id === 'backfill_lyrics' ? '将为已匹配曲目写入缺失歌词。' : '将搜索新候选并按音质规则替换指定曲目。' }}</p>
      <dl class="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-2 text-sm"><template v-for="field in confirmation.operation.fields" :key="field.key"><dt class="text-muted-foreground">{{ field.label }}</dt><dd class="break-words">{{ confirmation.body[field.key] == null || confirmation.body[field.key] === '' ? '不限 / 默认' : Array.isArray(confirmation.body[field.key]) ? confirmation.body[field.key].join('、') : confirmation.body[field.key] }}</dd></template></dl>
      <OperationResult v-if="previews[confirmation.operation.id]" :result="previews[confirmation.operation.id].result" />
      <label v-if="confirmation.operation.id === 'cleanup'" class="flex gap-2 items-start text-sm text-[var(--state-error)]"><input v-model="deleteConfirmed" type="checkbox" class="mm-checkbox mt-0.5 shrink-0" />我已确认上述删除范围</label>
      <template #footer><button class="mm-btn btn-ghost" :disabled="!!busy" @click="confirmation = null">取消</button><button class="mm-btn" :class="confirmation.operation.id === 'cleanup' ? 'btn-danger-ghost' : 'btn-primary'" :disabled="!!busy || (confirmation.operation.id === 'cleanup' && !deleteConfirmed)" @click="execute"><Loader2 v-if="busy" class="w-4 h-4 animate-spin" />执行</button></template>
    </ModalDialog>
  </AppShell>
</template>
<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { ArrowLeft, ArrowRightLeft, File, Folder, Languages, Loader2, Music2, RefreshCw, Replace, Trash2 } from 'lucide-vue-next'
import AppShell from '../components/AppShell.vue'
import ModalDialog from '../components/ModalDialog.vue'
import OperationResult from '../components/OperationResult.vue'
import SourcePicker from '../components/SourcePicker.vue'
import client, { errorMessage } from '../api/client'
import { useToastStore } from '../stores/toast'
import { formatSize } from '../utils/format'
const toast = useToastStore()
const libraries = ref([]), library = ref(''), sources = ref([]), loadingLibraries = ref(true), entries = ref([]), path = ref(''), hasMore = ref(false), browsing = ref(false), browseError = ref(''), busy = ref(''), confirmation = ref(null), deleteConfirmed = ref(false)
const anyBusy = computed(() => !!busy.value)
const results = reactive({}), previews = reactive({})
const forms = reactive({
  replace_track: { library: '', artist: '', album: '', track: '', sources: [], force: false, max_size_mb: '' },
  cleanup: { library: '', artist: '', album: '', tracks: '' },
  migrate_singles: { library: '', target_library: '', artist: '' },
  backfill_lyrics: { library: '', artist: '', album: '', sources: [], limit: 50 },
})
const libField = { key: 'library', label: '媒体库', type: 'library', wide: true }
const artistField = { key: 'artist', label: '艺人', placeholder: '库内艺人目录名' }
const albumField = { key: 'album', label: '专辑', placeholder: '库内专辑目录名' }
const operations = [
  { id: 'replace_track', title: '替换曲目', icon: Replace, description: '重搜指定曲目，仅候选音质更高时替换（可强制）。', fields: [libField, artistField, albumField, { key: 'track', label: '曲目序号或曲名', wide: true, placeholder: '例如：3、2-03 或曲名' }, { key: 'max_size_mb', label: '单文件上限（MB）', type: 'number', wide: true, placeholder: '留空使用配置' }] },
  { id: 'cleanup', title: '清理库', icon: Trash2, preview: true, description: '按艺人、专辑或指定曲目清理，删除同名歌词并清理空目录。', fields: [libField, artistField, { ...albumField, label: '专辑（可选）', placeholder: '留空则匹配艺人目录' }, { key: 'tracks', label: '曲目（可选，逗号或换行分隔）', wide: true, placeholder: '例如：01, 03；留空清理整个目录' }] },
  { id: 'migrate_singles', title: '迁移单曲', icon: ArrowRightLeft, preview: true, description: '将只有一首音频的专辑目录迁移到单曲库。', fields: [{ ...libField, label: '源库', wide: false }, { key: 'target_library', label: '目标库', type: 'library' }, { ...artistField, label: '艺人（可选）', wide: true, placeholder: '留空扫描源库' }] },
  { id: 'backfill_lyrics', title: '回填歌词', icon: Languages, preview: true, description: '搜索匹配并回填缺失 .lrc，已有歌词自动跳过。', fields: [libField, { ...artistField, label: '艺人（可选）', placeholder: '留空扫描全库' }, { ...albumField, label: '专辑（可选）', placeholder: '需同时填写艺人' }, { key: 'limit', label: '数量上限', type: 'number', wide: true }] },
]
let browseController
const lifecycle = new AbortController()
watch(library, value => { for (const form of Object.values(forms)) form.library = value; loadDirectory('') })
for (const operation of operations) watch(() => forms[operation.id], () => { previews[operation.id] = null; if (results[operation.id]?.dry_run) results[operation.id] = null }, { deep: true })
async function loadDirectory(nextPath = '', append = false) {
  if (!library.value) return
  browseController?.abort()
  const controller = browseController = new AbortController()
  browsing.value = true; browseError.value = ''
  const offset = append ? entries.value.length : 0
  if (!append) { entries.value = []; path.value = nextPath; hasMore.value = false }
  try { const { data } = await client.get('/library/entries', { params: { library: library.value, path: nextPath, offset }, signal: controller.signal }); entries.value = append ? [...entries.value, ...data.entries] : data.entries; hasMore.value = data.has_more }
  catch (e) { if (!controller.signal.aborted) { browseError.value = errorMessage(e, '目录读取失败'); toast.show(browseError.value, 'error') } }
  finally { if (!controller.signal.aborted) browsing.value = false }
}
function pickEntry(entry) {
  const parts = entry.path.split('/')
  if (entry.type === 'directory') {
    if (parts[0]) for (const form of Object.values(forms)) form.artist = parts[0]
    if (parts.length > 1) for (const op of ['replace_track', 'cleanup', 'backfill_lyrics']) forms[op].album = parts[1]
    loadDirectory(entry.path)
    return
  }
  for (const form of Object.values(forms)) form.artist = parts[0]
  for (const op of ['replace_track', 'cleanup', 'backfill_lyrics']) forms[op].album = parts.length > 2 ? parts[1] : ''
  const stem = entry.name.replace(/\.[^.]+$/, '')
  const disc = parts.at(-2)?.match(/^CD(\d+)$/i), track = stem.match(/^(\d+)/)
  const identifier = disc && track ? `${disc[1]}-${track[1]}` : stem
  forms.replace_track.track = identifier; forms.cleanup.tracks = identifier
}
function bodyFor(operation) {
  const form = forms[operation.id], body = { library: form.library }
  if (!form.library) throw new Error('请先选择媒体库')
  if (['replace_track', 'cleanup'].includes(operation.id) && !form.artist.trim()) throw new Error('艺人名不能为空')
  if (operation.id === 'replace_track' && (!form.album.trim() || !form.track.trim())) throw new Error('专辑名和曲目不能为空')
  if (operation.id === 'backfill_lyrics' && form.album.trim() && !form.artist.trim()) throw new Error('指定专辑时须填写艺人')
  if (operation.id === 'migrate_singles' && (!form.target_library || form.target_library === form.library)) throw new Error('请选择与源库不同的目标库')
  for (const key of ['artist', 'album', 'track', 'target_library']) if (form[key]?.trim()) body[key] = form[key].trim()
  if (form.tracks?.trim()) { body.tracks = form.tracks.split(/[,，\n]/).map(t => t.trim()).filter(Boolean).map(t => /^\d+$/.test(t) ? Number(t) : t); if (!body.tracks.length) throw new Error('请输入有效的曲目序号或曲名') }
  if (form.sources?.length) body.sources = [...form.sources]
  if ('force' in form) body.force = form.force
  if ('limit' in form) { const limit = Number(form.limit); if (!Number.isInteger(limit) || limit < 1) throw new Error('数量上限须为正整数'); body.limit = limit }
  if ('max_size_mb' in form && form.max_size_mb !== '') { const max = Number(form.max_size_mb); if (!Number.isFinite(max) || max < 0) throw new Error('大小上限须为非负数'); if (max > 0) body.max_size_mb = max }
  return body
}
async function preview(operation) {
  if (busy.value) return
  let body
  try { body = bodyFor(operation) } catch (e) { toast.show(e.message, 'warning'); return }
  busy.value = operation.id; previews[operation.id] = null
  try {
    const { data } = await client.post(`/library/${operation.id}`, { ...body, dry_run: true }, { timeout: 600000, signal: lifecycle.signal })
    results[operation.id] = data
    if (data.status === 'success' && !(data.errors?.length)) previews[operation.id] = { body, result: data }
  } catch (e) { if (!lifecycle.signal.aborted) toast.show(errorMessage(e, '预览失败'), 'error') }
  finally { busy.value = '' }
}
function openConfirm(operation) {
  if (busy.value) return
  try {
    const body = operation.preview ? previews[operation.id]?.body : bodyFor(operation)
    if (!body) return
    if (operation.preview && JSON.stringify(bodyFor(operation)) !== JSON.stringify(body)) { previews[operation.id] = null; toast.show('操作范围已变化，请重新预览', 'warning'); return }
    deleteConfirmed.value = false; confirmation.value = { operation, body: JSON.parse(JSON.stringify(body)) }
  } catch (e) { toast.show(e.message, 'warning') }
}
async function execute() {
  if (busy.value || !confirmation.value) return
  const { operation, body } = confirmation.value
  if (operation.id === 'cleanup' && !deleteConfirmed.value) return
  busy.value = operation.id
  try {
    const request = { ...body }
    if (operation.preview) request.dry_run = false
    if (operation.id === 'cleanup') request.confirm = true
    const { data } = await client.post(`/library/${operation.id}`, request, { timeout: 600000, signal: lifecycle.signal })
    results[operation.id] = data; previews[operation.id] = null; confirmation.value = null
    const ok = data.status === 'success' && !['failed', 'unmatched'].includes(data.action)
    toast.show(ok ? `${operation.title}处理完成` : '部分项目未成功，请查看结果', ok ? 'success' : 'warning')
    loadDirectory('')
  } catch (e) { if (!lifecycle.signal.aborted) toast.show(errorMessage(e, '执行失败，请查看目录状态后重新预览'), 'error'); previews[operation.id] = null; confirmation.value = null }
  finally { busy.value = '' }
}
onMounted(async () => {
  const responses = await Promise.allSettled([client.get('/libraries', { signal: lifecycle.signal }), client.get('/sources', { signal: lifecycle.signal })])
  if (lifecycle.signal.aborted) return
  if (responses[0].status === 'fulfilled') { libraries.value = responses[0].value.data; library.value = (libraries.value.find(l => l.default) || libraries.value[0])?.name || ''; forms.migrate_singles.target_library = libraries.value.find(l => l.name === 'singles')?.name || libraries.value.find(l => l.name !== library.value)?.name || '' }
  else toast.show(errorMessage(responses[0].reason, '获取媒体库失败'), 'error')
  if (responses[1].status === 'fulfilled') sources.value = responses[1].value.data.filter(s => s.available && s.supports_search)
  else toast.show(errorMessage(responses[1].reason, '获取音源失败'), 'error')
  loadingLibraries.value = false
})
onBeforeUnmount(() => { lifecycle.abort(); browseController?.abort() })
</script>
