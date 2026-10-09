<template>
  <AppShell>
    <header class="flex flex-wrap items-center justify-between gap-3 mb-5"><div><h1 class="text-xl font-semibold">飞牛歌单</h1><p class="text-sm text-muted-foreground mt-1">浏览飞牛音乐的歌单与已入库曲目。</p></div><div class="flex flex-wrap gap-2"><button class="mm-btn btn-outline" :disabled="loading || creating" @click="load"><RefreshCw class="w-4 h-4" :class="{ 'animate-spin': loading }" />刷新</button><button v-if="configured" class="mm-btn btn-primary" @click="createOpen = true"><Plus class="w-4 h-4" />创建歌单</button></div></header>
    <p v-if="error" class="mm-notice mb-4 text-[var(--state-error)]" role="alert">{{ error }}</p>
    <section v-if="configured === false" class="mm-card p-6 space-y-3"><Server class="w-7 h-7 text-primary" /><h2 class="font-semibold">先连接飞牛音乐</h2><p class="text-sm text-muted-foreground">在系统配置中填写飞牛音乐应用地址、账号和库路径映射，保存后即可使用。</p><RouterLink to="/settings?section=fnos" class="mm-btn btn-primary">配置飞牛连接</RouterLink></section>
    <p v-else-if="configured === null" class="mm-card p-8 text-sm text-muted-foreground" role="status">{{ loading ? '正在读取飞牛连接配置…' : '未能读取配置，请点击刷新重试。' }}</p>
    <template v-else>
      <form class="mm-card p-4 flex gap-2 mb-5" @submit.prevent="search"><label class="flex-1 min-w-0"><span class="sr-only">搜索飞牛音乐</span><input v-model="query" class="mm-input" placeholder="搜索曲目、专辑、艺人或歌单" /></label><button class="mm-btn btn-primary" :disabled="searching || !query.trim()"><Search class="w-4 h-4" />搜索</button><button v-if="suggest || searchError" type="button" class="mm-btn btn-ghost" @click="clearSearch">清除</button></form>
      <p v-if="searchError" class="mm-notice mb-4 text-[var(--state-error)]" role="alert">{{ searchError }}</p>
      <p v-if="searching" class="text-sm text-muted-foreground mb-4" role="status">正在搜索飞牛音乐…</p>
      <section v-if="suggest" class="space-y-4 mb-6"><div class="flex flex-wrap justify-between gap-3 items-center"><h2 class="text-sm font-semibold">“{{ searchedQuery }}”的搜索结果</h2><button class="mm-btn btn-outline" :disabled="loadingTracks" @click="fullTracks"><LoaderCircle v-if="loadingTracks" class="w-4 h-4 animate-spin" />查看全部曲目</button></div>
        <div class="grid gap-4 sm:grid-cols-2 lg:grid-cols-4"><section v-for="group in searchGroups" :key="group.id" class="mm-card p-4 min-w-0"><h3 class="text-sm font-medium mb-3">{{ group.title }} · {{ suggest[group.id]?.total || 0 }}</h3><p v-if="!suggest[group.id]?.items?.length" class="text-xs text-muted-foreground">没有匹配结果</p><div v-else class="space-y-3"><div v-for="item in suggest[group.id].items" :key="item.guid" class="text-xs min-w-0"><button v-if="group.id === 'playlist'" class="text-left text-primary break-words" @click="openPlaylist(item.name)">{{ item.name }}</button><template v-else><p class="font-medium break-words">{{ item.title || item.name }}</p><p v-if="item.artists?.length" class="text-muted-foreground mt-1 break-words">{{ item.artists.join(' / ') }}</p><p v-if="item.track_count != null" class="text-muted-foreground mt-1">{{ item.track_count }} 首曲目</p></template></div></div></section></div>
        <div v-if="suggest.track?.items?.length || foundTracks"><div class="flex flex-wrap gap-3 items-center mb-3"><span class="text-xs text-muted-foreground">已选择 {{ selected.length }} 首{{ foundTracks ? ` · 展示前 ${foundTracks.items.length} / ${foundTracks.total} 首` : ' · 搜索推荐展示前 5 首' }}</span><button class="mm-btn btn-primary" :disabled="!selected.length" @click="chooseTarget"><ListPlus class="w-4 h-4" />追加到歌单</button></div><FnosTrackList v-model="selected" :tracks="foundTracks?.items || suggest.track.items" :loading="loadingTracks" selectable title="匹配曲目" /></div>
      </section>
    <section v-if="route.params.name" class="space-y-4"><div class="flex flex-wrap items-center justify-between gap-3"><div class="min-w-0"><RouterLink to="/fnos" class="text-xs text-primary">← 返回全部歌单</RouterLink><h2 class="font-semibold mt-2 break-words">{{ route.params.name }}</h2></div><button class="mm-btn btn-primary" :disabled="detailLoading || !detail" @click="appendName = String(route.params.name); initialAppend = []"><ListPlus class="w-4 h-4" />追加曲目</button></div><p v-if="detailError" class="mm-notice" role="alert">{{ detailError }}<button class="mm-btn btn-ghost" @click="loadDetail">重试</button></p><FnosTrackList v-if="!detailError" :tracks="detail?.tracks || []" :loading="detailLoading" title="歌单曲目" empty-text="这是一个空歌单，可以追加飞牛已入库曲目。" /></section>
      <section v-else><div class="flex flex-wrap items-center justify-between gap-3 mb-4"><h2 class="text-sm font-semibold">全部歌单 · {{ playlists.length }}</h2><label class="max-w-full w-56"><span class="sr-only">筛选歌单名称</span><input v-model="playlistFilter" class="mm-input" placeholder="按歌单名称筛选" /></label></div><p v-if="loading && !playlists.length" class="mm-card p-8 text-sm text-muted-foreground" role="status">正在读取歌单…</p><p v-else-if="!filteredPlaylists.length" class="mm-card p-8 text-sm text-muted-foreground">{{ playlistFilter ? '没有匹配的歌单' : error ? '未能读取歌单，请检查连接后重试。' : '暂无歌单，创建一个空歌单开始整理音乐。' }}</p><div v-else class="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4"><button v-for="playlist in filteredPlaylists" :key="playlist.guid" class="mm-card text-left p-4 hover:border-primary transition-colors min-w-0" @click="openPlaylist(playlist.name)"><span class="inline-flex w-10 h-10 rounded-md bg-muted items-center justify-center text-primary mb-3"><ListMusic class="w-5 h-5" /></span><h3 class="font-medium text-sm break-words">{{ playlist.name }}</h3><p class="text-xs text-muted-foreground mt-2">{{ playlist.track_count == null ? '数量暂不可用' : `${playlist.track_count} 首曲目` }}</p></button></div></section>
    </template>
    <ModalDialog v-if="createOpen" title="创建飞牛歌单" :busy="creating" width="32rem" @close="createOpen = false">
      <form v-if="!createResult" id="fnos-create" class="space-y-4" @submit.prevent="create"><label class="mm-field">歌单名称<input v-model="newName" class="mm-input" placeholder="例如：通勤音乐" :disabled="creating" maxlength="100" required /></label><CompletedTaskPicker v-model="newTaskId" :disabled="creating" optional /><p class="text-xs text-muted-foreground">可先创建空歌单，也可导入任务。已有同名歌单时会打开或补充该歌单。</p></form>
      <p v-if="creating" class="text-sm text-primary" role="status">正在创建并同步曲目，等待飞牛扫描时可能需要 {{ scanWait }} 秒…</p><p v-if="createError" class="text-sm text-[var(--state-error)]" role="alert">{{ createError }}</p>
      <div v-if="createResult" class="text-sm space-y-3" role="status"><p>歌单已保存 · 新增 {{ createResult.added }} 首 · 已存在 {{ createResult.already }} 首</p><p class="text-[var(--state-warning)]">有 {{ createResult.unresolved.length }} 个文件尚未匹配；请检查映射或扫描状态后重试。</p><ul class="max-h-48 overflow-auto space-y-1"><li v-for="path in createResult.unresolved" :key="path" class="mono break-all">{{ path }}</li></ul></div>
      <template #footer><button class="mm-btn btn-outline" :disabled="creating" @click="createOpen = false">{{ createResult ? '关闭' : '取消' }}</button><button v-if="createResult" class="mm-btn btn-primary" @click="createOpen = false; openPlaylist(createResult.playlist_name)">打开歌单</button><button v-else type="submit" form="fnos-create" class="mm-btn btn-primary" :disabled="creating || !newName.trim()">{{ creating ? '正在创建…' : '创建歌单' }}</button></template>
    </ModalDialog>
    <ModalDialog v-if="targetOpen" title="选择目标歌单" width="28rem" @close="targetOpen = false"><label class="mm-field">目标歌单<select v-model="targetName" class="mm-input"><option value="">请选择歌单</option><option v-for="playlist in playlists" :key="playlist.guid" :value="playlist.name">{{ playlist.name }}</option></select></label><p v-if="!playlists.length" class="text-sm text-muted-foreground">请先创建歌单。</p><template #footer><button class="mm-btn btn-outline" @click="targetOpen = false">取消</button><button class="mm-btn btn-primary" :disabled="!targetName" @click="confirmTarget">继续</button></template></ModalDialog>
    <FnosAppendDialog v-if="appendName" :name="appendName" :initial-tracks="initialAppend" :scan-wait="scanWait" @close="appendName = null" @saved="appendSaved" @busy="appendBusy = $event" />
  </AppShell>
</template>
<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'
import { ListMusic, ListPlus, LoaderCircle, Plus, RefreshCw, Search, Server } from 'lucide-vue-next'
import AppShell from '../components/AppShell.vue'
import ModalDialog from '../components/ModalDialog.vue'
import FnosTrackList from '../components/FnosTrackList.vue'
import FnosAppendDialog from '../components/FnosAppendDialog.vue'
import CompletedTaskPicker from '../components/CompletedTaskPicker.vue'
import client, { errorMessage } from '../api/client'
import { useToastStore } from '../stores/toast'
const route = useRoute(), router = useRouter(), toast = useToastStore()
const configured = ref(null), scanWait = ref(120), playlists = ref([]), loading = ref(false), error = ref(''), playlistFilter = ref(''), detail = ref(null), detailLoading = ref(false), detailError = ref(''), query = ref(''), searchedQuery = ref(''), searching = ref(false), searchError = ref(''), suggest = ref(null), foundTracks = ref(null), loadingTracks = ref(false), selected = ref([]), createOpen = ref(false), creating = ref(false), newName = ref(''), createError = ref(''), targetOpen = ref(false), targetName = ref(''), appendName = ref(null), initialAppend = ref([])
let disposed = false, loadVersion = 0, detailVersion = 0, searchVersion = 0
const appendBusy = ref(false)
const newTaskId = ref(''), createResult = ref(null)
const searchGroups = [{ id: 'track', title: '曲目' }, { id: 'album', title: '专辑' }, { id: 'artist', title: '艺人' }, { id: 'playlist', title: '歌单' }]
const filteredPlaylists = computed(() => playlists.value.filter(p => p.name.toLowerCase().includes(playlistFilter.value.trim().toLowerCase())))
function openPlaylist(name) { router.push({ name: 'fnos-detail', params: { name } }) }
async function load() {
  const version = ++loadVersion; loading.value = true; error.value = ''
  try {
    const { data: config } = await client.get('/config')
    if (disposed || version !== loadVersion) return
    configured.value = !!config.fnos_music; scanWait.value = config.fnos_music?.scan_wait_s ?? 120
    if (!configured.value) { playlists.value = []; return }
    const { data } = await client.get('/fnos/playlists')
    if (!disposed && version === loadVersion) { playlists.value = data; if (route.params.name) loadDetail() }
  } catch (e) { if (!disposed && version === loadVersion) error.value = errorMessage(e) }
  finally { if (!disposed && version === loadVersion) loading.value = false }
}
async function loadDetail() {
  const name = route.params.name, version = ++detailVersion
  if (!name || !configured.value) return
  detailLoading.value = true; detailError.value = ''; detail.value = null
  try {
    const { data } = await client.get(`/fnos/playlists/${encodeURIComponent(name)}/tracks`)
    if (!disposed && version === detailVersion) {
      detail.value = data
      const playlist = playlists.value.find(item => item.guid === data.playlist_guid)
      if (playlist) playlist.track_count = data.count
    }
  }
  catch (e) { if (!disposed && version === detailVersion) detailError.value = errorMessage(e) }
  finally { if (!disposed && version === detailVersion) detailLoading.value = false }
}
async function search() {
  if (!query.value.trim() || searching.value) return
  const version = ++searchVersion; searching.value = true; searchError.value = ''; foundTracks.value = null; selected.value = []; suggest.value = null; searchedQuery.value = query.value.trim()
  try { const { data } = await client.get('/fnos/search', { params: { q: searchedQuery.value } }); if (!disposed && version === searchVersion) suggest.value = data }
  catch (e) { if (!disposed && version === searchVersion) searchError.value = errorMessage(e) }
  finally { if (!disposed && version === searchVersion) searching.value = false }
}
function clearSearch() { searchVersion++; query.value = ''; suggest.value = null; foundTracks.value = null; searchError.value = ''; selected.value = []; searching.value = false; loadingTracks.value = false }
async function fullTracks() {
  const version = searchVersion; loadingTracks.value = true; searchError.value = ''
  try { const { data } = await client.get('/fnos/search/tracks', { params: { q: searchedQuery.value, limit: 200 } }); if (!disposed && version === searchVersion) { foundTracks.value = data; selected.value = [] } }
  catch (e) { if (!disposed && version === searchVersion) searchError.value = errorMessage(e) }
  finally { if (!disposed && version === searchVersion) loadingTracks.value = false }
}
async function create() {
  const name = newName.value.trim(); if (!name || creating.value) return
  if (!newTaskId.value && playlists.value.some(p => p.name === name)) { createOpen.value = false; openPlaylist(name); return }
  creating.value = true; createError.value = ''
  try {
    const { data } = await client.post('/fnos/playlists', { name, ...(newTaskId.value ? { task_id: newTaskId.value } : {}) }, { timeout: Math.max(120000, (scanWait.value + 90) * 1000) })
    if (disposed) return
    toast.show(`歌单已保存 · 新增 ${data.added} 首`); await load()
    if (data.status === 'partial') createResult.value = data
    else { creating.value = false; createOpen.value = false; openPlaylist(name) }
  }
  catch (e) { if (!disposed) createError.value = errorMessage(e) }
  finally { if (!disposed) creating.value = false }
}
function chooseTarget() { if (route.params.name && detail.value) { targetName.value = String(route.params.name); confirmTarget() } else { targetName.value = ''; targetOpen.value = true } }
function confirmTarget() { initialAppend.value = (foundTracks.value?.items || suggest.value?.track?.items || []).filter(t => selected.value.includes(t.guid)); appendName.value = targetName.value; targetOpen.value = false }
async function appendSaved(result) { selected.value = []; toast.show(`新增 ${result.added} 首，已存在 ${result.already} 首`); await load() }
watch(() => route.params.name, () => { detailVersion++; detail.value = null; detailError.value = ''; detailLoading.value = false; if (route.params.name) loadDetail() })
watch(createOpen, value => { if (value) { createError.value = ''; createResult.value = null; newName.value = ''; newTaskId.value = '' } })
onMounted(load)
function allowNavigation() { if (creating.value || appendBusy.value) { toast.show('歌单操作正在执行，请等待完成', 'info'); return false } return true }
onBeforeRouteLeave(allowNavigation)
onBeforeRouteUpdate(allowNavigation)
onBeforeUnmount(() => { disposed = true; loadVersion++; detailVersion++; searchVersion++ })
</script>
