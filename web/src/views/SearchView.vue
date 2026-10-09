<template>
  <AppShell>
    <div class="space-y-5">
      <!-- ===== 搜索栏 ===== -->
      <section class="bg-card rounded-lg border border-border p-5 space-y-4" style="box-shadow: var(--mm-shadow-sm);">
        <div class="flex flex-wrap items-center gap-3">
          <div class="relative flex-1 min-w-0 basis-full sm:basis-auto">
            <Search class="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
            <input
              v-model="keyword"
              type="text"
              placeholder="输入歌曲名、歌手或专辑关键词..."
              class="w-full h-10 pl-10 pr-4 rounded-md border border-input bg-background text-sm focus:outline-none focus:ring-2 focus:ring-ring focus:border-transparent placeholder:text-muted-foreground"
              @keyup.enter="doSearch"
            />
          </div>
          <select v-model="limit" class="h-10 px-3 rounded-md border border-input bg-background text-sm focus:outline-none focus:ring-2 focus:ring-ring">
            <option :value="20">20 条</option>
            <option :value="50">50 条</option>
            <option :value="100">100 条</option>
          </select>
          <button class="btn-lift h-10 px-6 rounded-md bg-primary text-primary-foreground text-sm font-medium flex items-center gap-2" :disabled="searching || sourcesLoading" @click="doSearch">
            <Loader2 v-if="searching" class="w-4 h-4 animate-spin" />
            <Search v-else class="w-4 h-4" />
            搜索
          </button>
        </div>

        <!-- 数据源多选 -->
        <div class="flex items-center gap-2 flex-wrap">
          <span class="text-xs text-muted-foreground mr-1">数据源：</span>
          <button
            v-for="src in searchSources"
            :key="src.name"
            class="source-chip px-3 py-1.5 rounded-full border border-border text-xs font-medium bg-background"
            :data-active="selectedSources.has(src.name) ? 'true' : 'false'"
            :title="src.note || src.name"
            :aria-pressed="selectedSources.has(src.name)"
            :class="{ 'opacity-60': !src.available }"
            @click="toggleSource(src.name)"
          >
            {{ sourceDisplayName(src.name) }}<span v-if="!src.available"> · 不可用</span>
          </button>
        </div>
      </section>

      <p v-if="sourcesLoading" class="text-xs text-muted-foreground" role="status">正在同步默认搜索来源…</p>
      <p v-else-if="!sourcesReady" class="mm-notice">未能读取搜索来源。<button class="mm-btn btn-ghost" @click="loadSources">重新获取</button></p>

      <!-- ===== 失败源警告 ===== -->
      <div v-if="failedSources.length" class="flex items-start gap-2 px-4 py-3 rounded-md border" style="border-color: color-mix(in srgb, var(--state-warning) 30%, transparent); background: color-mix(in srgb, var(--state-warning) 5%, transparent);">
        <AlertTriangle class="w-4 h-4 mt-0.5 shrink-0" :style="{ color: 'var(--state-warning)' }" />
        <div class="text-sm">
          <span class="font-medium" :style="{ color: 'var(--state-warning)' }">部分数据源搜索失败：</span>
          <span class="text-muted-foreground">{{ failedSourceText }}</span>
        </div>
      </div>

      <!-- ===== 搜索结果 ===== -->
      <section v-if="searched" class="bg-card rounded-lg border border-border" style="box-shadow: var(--mm-shadow-sm);">
        <div class="px-5 py-4 border-b border-border flex flex-wrap items-center justify-between gap-3">
          <div class="flex items-center gap-2">
            <h2 class="text-sm font-semibold">搜索结果</h2>
            <span class="text-xs text-muted-foreground">共 {{ tracks.length }} 条结果，来自 {{ resultSourceCount }} 个数据源</span>
          </div>
          <div class="flex flex-wrap items-center gap-3">
            <label class="flex items-center gap-2 text-xs text-muted-foreground">排序
              <select v-model="sortOrder" class="h-8 px-2 rounded-md border border-input bg-background text-xs" aria-label="搜索结果排序">
                <option value="original">默认顺序</option>
                <option value="size-desc">文件大小：从大到小</option>
                <option value="size-asc">文件大小：从小到大</option>
              </select>
            </label>
            <button class="text-xs text-muted-foreground hover:text-foreground" :disabled="!tracks.length" @click="toggleSelectAll">{{ allSelected ? '取消全选' : '全选' }}</button>
            <button class="text-xs text-muted-foreground hover:text-foreground flex items-center gap-1" :disabled="searching || sourcesLoading" @click="doSearch">
              <RefreshCw class="w-3.5 h-3.5" :class="{ 'animate-spin': searching }" />
              刷新
            </button>
          </div>
        </div>

        <div v-if="searching && !tracks.length" class="py-12 text-center text-sm text-muted-foreground">搜索中，请稍候...</div>
        <div v-else-if="!tracks.length" class="py-12 text-center text-sm text-muted-foreground">没有找到相关结果，换个关键词试试</div>

        <template v-else>
          <!-- 桌面表格 -->
          <div class="table-container hidden min-[960px]:block">
            <table class="w-full text-sm">
              <thead>
                <tr class="border-b border-border">
                  <th class="w-10 px-4 py-3 text-left">
                    <input type="checkbox" class="w-4 h-4 rounded border-input text-primary focus:ring-ring" style="accent-color: var(--mm-primary);" :checked="allSelected" :indeterminate="selected.size > 0 && !allSelected" aria-label="全选搜索结果" @change="toggleSelectAll" />
                  </th>
                  <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">标题</th>
                  <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">歌手</th>
                  <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">专辑</th>
                  <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">时长</th>
                  <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">音质</th>
                  <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">格式</th>
                  <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">大小</th>
                  <th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">来源</th>
                </tr>
              </thead>
              <tbody class="divide-y divide-border">
                <tr v-for="track in sortedTracks" :key="track.id" class="transition-colors">
                  <td class="px-4 py-3">
                    <input type="checkbox" class="w-4 h-4 rounded border-input text-primary focus:ring-ring" style="accent-color: var(--mm-primary);" :checked="selected.has(track.id)" @change="toggleTrack(track.id)" />
                  </td>
                  <td class="px-4 py-3 font-medium">{{ track.title }}</td>
                  <td class="px-4 py-3 text-muted-foreground">{{ artistsText(track) }}</td>
                  <td class="px-4 py-3 text-muted-foreground">{{ track.album || '—' }}</td>
                  <td class="px-4 py-3 text-muted-foreground font-mono text-xs">{{ formatDuration(track.duration_s) }}</td>
                  <td class="px-4 py-3">
                    <span class="inline-flex px-1.5 py-0.5 rounded text-[10px] font-medium" :style="qualityBadgeStyle(track.quality)">{{ qualityInfo(track.quality).label }}</span>
                  </td>
                  <td class="px-4 py-3 text-muted-foreground font-mono text-xs">{{ (track.ext || '—').toUpperCase() }}</td>
                  <td class="px-4 py-3 text-muted-foreground font-mono text-xs">{{ formatSize(track.size_bytes) }}</td>
                  <td class="px-4 py-3"><span class="text-xs text-muted-foreground">{{ sourceDisplayName(track.source) }}</span></td>
                </tr>
              </tbody>
            </table>
          </div>

          <!-- 移动端卡片列表 -->
          <div class="min-[960px]:hidden divide-y divide-border">
            <label v-for="track in sortedTracks" :key="track.id" class="flex items-start gap-3 px-4 py-3 cursor-pointer">
              <input type="checkbox" class="mt-1 w-4 h-4 rounded border-input text-primary focus:ring-ring shrink-0" style="accent-color: var(--mm-primary);" :checked="selected.has(track.id)" @change="toggleTrack(track.id)" />
              <div class="min-w-0 flex-1">
                <div class="flex items-center gap-2">
                  <span class="font-medium text-sm truncate">{{ track.title }}</span>
                  <span class="inline-flex px-1.5 py-0.5 rounded text-[10px] font-medium shrink-0" :style="qualityBadgeStyle(track.quality)">{{ qualityInfo(track.quality).label }}</span>
                </div>
                <div class="mt-0.5 text-xs text-muted-foreground truncate">{{ artistsText(track) }}<template v-if="track.album"> · {{ track.album }}</template></div>
                <div class="mt-1 flex items-center gap-2 text-xs text-muted-foreground font-mono">
                  <span>{{ formatDuration(track.duration_s) }}</span>
                  <span>{{ (track.ext || '—').toUpperCase() }}</span>
                  <span>{{ formatSize(track.size_bytes) }}</span>
                  <span class="font-sans">{{ sourceDisplayName(track.source) }}</span>
                </div>
              </div>
            </label>
          </div>
        </template>
      </section>

      <!-- ===== 已选操作底栏 ===== -->
      <section v-if="searched" class="bg-card rounded-lg border border-border px-5 py-4 flex flex-wrap items-center justify-between gap-3" style="box-shadow: var(--mm-shadow-sm);">
        <div class="flex items-center gap-3">
          <span class="text-sm text-muted-foreground">已选 <span class="font-semibold text-foreground">{{ selected.size }}</span> 首</span>
          <span class="text-xs text-muted-foreground">|</span>
          <span class="text-xs text-muted-foreground">总计约 {{ selectedSizeText }}</span>
        </div>
        <div class="flex items-center gap-2">
          <button class="h-9 px-4 rounded-md border border-border text-sm text-muted-foreground hover:text-foreground hover:bg-muted transition-colors" :disabled="!selected.size" @click="clearSelection">
            清空选择
          </button>
          <button class="btn-lift h-9 px-5 rounded-md bg-primary text-primary-foreground text-sm font-medium flex items-center gap-1.5 disabled:opacity-60" :disabled="!selected.size" @click="openDownloadModal">
            <Download class="w-4 h-4" />
            提交下载
          </button>
        </div>
      </section>
    </div>

    <!-- ===== 下载选项对话框 ===== -->
    <div v-if="downloadModalOpen" class="modal-overlay" @click.self="downloadModalOpen = false">
      <div class="relative bg-card rounded-lg border border-border w-full max-w-md" style="box-shadow: var(--mm-shadow-lg);">
        <div class="px-6 py-4 border-b border-border flex items-center justify-between">
          <h3 class="text-sm font-semibold">提交下载任务</h3>
          <button class="text-muted-foreground hover:text-foreground transition-colors" aria-label="关闭" @click="downloadModalOpen = false">
            <X class="w-4 h-4" />
          </button>
        </div>
        <div class="px-6 py-5 space-y-4">
          <div>
            <label class="text-xs font-medium text-muted-foreground block mb-1.5">目标媒体库</label>
            <select v-model="downloadForm.library" class="w-full h-9 px-3 rounded-md border border-input bg-background text-sm focus:outline-none focus:ring-2 focus:ring-ring">
              <option value="">不归档（仅下载）</option>
              <option v-for="lib in libraries" :key="lib.name" :value="lib.name">{{ lib.name }}{{ lib.default ? '（默认库）' : '' }}</option>
            </select>
          </div>
          <div>
            <label class="text-xs font-medium text-muted-foreground block mb-1.5">保存子目录</label>
            <input v-model.trim="downloadForm.subdir" type="text" placeholder="例如：周杰伦/叶惠美" class="w-full h-9 px-3 rounded-md border border-input bg-background text-sm focus:outline-none focus:ring-2 focus:ring-ring placeholder:text-muted-foreground" />
          </div>
          <div>
            <label class="text-xs font-medium text-muted-foreground block mb-1.5">单文件大小上限 (MB)</label>
            <input v-model.number="downloadForm.maxSizeMb" type="number" placeholder="0 或留空不限" min="0" max="500" class="w-full h-9 px-3 rounded-md border border-input bg-background text-sm focus:outline-none focus:ring-2 focus:ring-ring placeholder:text-muted-foreground" />
          </div>
          <div>
            <label class="text-xs font-medium text-muted-foreground block mb-1.5">飞牛歌单名</label>
            <input v-model.trim="downloadForm.playlist" type="text" placeholder="留空不同步；需搭配目标媒体库" class="w-full h-9 px-3 rounded-md border border-input bg-background text-sm focus:outline-none focus:ring-2 focus:ring-ring placeholder:text-muted-foreground" />
          </div>
          <p class="text-xs text-muted-foreground">已选 {{ selected.size }} 首，提交后可在任务中心查看进度。</p>
        </div>
        <div class="px-6 py-4 border-t border-border flex items-center justify-end gap-2">
          <button class="h-9 px-4 rounded-md border border-border text-sm text-muted-foreground hover:text-foreground hover:bg-muted transition-colors" :disabled="submitting" @click="downloadModalOpen = false">取消</button>
          <button class="btn-lift h-9 px-5 rounded-md bg-primary text-primary-foreground text-sm font-medium flex items-center gap-2 disabled:opacity-60" :disabled="submitting" @click="submitDownload">
            <Loader2 v-if="submitting" class="w-4 h-4 animate-spin" />
            确认提交
          </button>
        </div>
      </div>
    </div>
  </AppShell>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'
import { useSearchStore } from '../stores/search'
import { Search, RefreshCw, Download, AlertTriangle, X, Loader2 } from 'lucide-vue-next'
import AppShell from '../components/AppShell.vue'
import client, { errorMessage } from '../api/client'
import { useToastStore } from '../stores/toast'
import { formatDuration, formatSize, qualityInfo, sourceDisplayName } from '../utils/format'

const toast = useToastStore()
const router = useRouter()
const route = useRoute()

const searchStore = useSearchStore()
const { keyword, limit, searchSources, selectedSources, tracks, sortedTracks, sortOrder, failedSources, searched, searching, selected, sourcesLoading, sourcesReady } = storeToRefs(searchStore)
const { loadSources, doSearch } = searchStore
watch(() => route.query.keyword, (value) => {
  if (typeof value === 'string' && value !== searchStore.prefilledKeyword) {
    keyword.value = value
    searchStore.prefilledKeyword = value
  } else if (value == null) searchStore.prefilledKeyword = null
}, { immediate: true })

const libraries = ref([])
const downloadModalOpen = ref(false)
const submitting = ref(false)
const downloadForm = reactive({ library: '', subdir: '', maxSizeMb: null, playlist: '' })

const allSelected = computed(() => tracks.value.length > 0 && tracks.value.every((t) => selected.value.has(t.id)))
const resultSourceCount = computed(() => new Set(tracks.value.map((t) => t.source)).size)

const selectedSizeText = computed(() => {
  let total = 0
  let unknown = false
  for (const t of tracks.value) {
    if (!selected.value.has(t.id)) continue
    if (t.size_bytes) total += t.size_bytes
    else unknown = true
  }
  return `${formatSize(total)}${unknown ? '+' : ''}`
})

const failedSourceText = computed(() =>
  failedSources.value
    .map((f) => {
      if (typeof f === 'string') return `${sourceDisplayName(f)} 搜索失败`
      const name = sourceDisplayName(f.source || f.name)
      return f.error ? `${name} 搜索失败：${f.error}` : `${name} 搜索失败`
    })
    .join('；'),
)

function artistsText(track) {
  return Array.isArray(track.artists) ? track.artists.join('/') : (track.artists || '—')
}

function qualityBadgeStyle(quality) {
  const tier = qualityInfo(quality).tier
  const color = tier === 'lossless' ? 'var(--state-success)' : tier === 'high' ? 'var(--state-info)' : 'var(--state-warning)'
  return { background: `color-mix(in srgb, ${color} 10%, transparent)`, color }
}

function toggleSource(name) {
  if (selectedSources.value.has(name)) selectedSources.value.delete(name)
  else selectedSources.value.add(name)
}

function toggleTrack(id) {
  if (selected.value.has(id)) selected.value.delete(id)
  else selected.value.add(id)
}

function toggleSelectAll() {
  if (allSelected.value) tracks.value.forEach((t) => selected.value.delete(t.id))
  else tracks.value.forEach((t) => selected.value.add(t.id))
}

function clearSelection() {
  selected.value.clear()
}

async function openDownloadModal() {
  downloadModalOpen.value = true
  if (!libraries.value.length) {
    try {
      const { data } = await client.get('/libraries')
      libraries.value = data
    } catch {
      libraries.value = []
    }
  }
}

async function submitDownload() {
  if (submitting.value || !selected.value.size) return
  if (downloadForm.playlist && !downloadForm.library) {
    toast.show('同步飞牛歌单需要先选择目标媒体库', 'warning')
    return
  }
  submitting.value = true
  try {
    const body = { tracks: tracks.value.filter(track => selected.value.has(track.id)) }
    if (downloadForm.subdir) body.subdir = downloadForm.subdir
    if (downloadForm.library) body.library = downloadForm.library
    if (downloadForm.maxSizeMb > 0) body.max_size_mb = downloadForm.maxSizeMb
    if (downloadForm.playlist) body.playlist = downloadForm.playlist
    await client.post('/downloads', body)
    toast.show('下载任务已提交', 'success')
    downloadModalOpen.value = false
    router.push('/tasks')
  } catch (e) {
    toast.show(errorMessage(e, '提交下载失败'), 'error')
  } finally {
    submitting.value = false
  }
}

onMounted(loadSources)
</script>
