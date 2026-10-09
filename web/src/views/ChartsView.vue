<template>
  <AppShell>
    <div class="space-y-5">
      <div class="flex flex-wrap justify-between gap-3"><div><h1 class="text-xl font-semibold">榜单浏览</h1><p class="text-sm text-muted-foreground mt-1">浏览官方排行榜，解析可下载曲目。</p></div><button class="mm-btn btn-ghost" :disabled="loading" @click="loadCharts"><RefreshCw class="w-4 h-4" :class="{ 'animate-spin': loading }" />刷新目录</button></div>
      <div class="tab-bar" role="tablist" aria-label="榜单平台"><button v-for="item in platforms" :key="item.id" class="tab-item" role="tab" :aria-selected="source === item.id" :class="{ active: source === item.id }" @click="changeSource(item.id)">{{ item.label }}</button></div>
      <p v-if="loading" class="text-sm text-muted-foreground" role="status">正在加载榜单目录…</p>
      <p v-else-if="!charts.length" class="mm-card p-8 text-center text-sm text-muted-foreground">暂无榜单，请刷新重试。</p>
      <section class="grid grid-cols-2 lg:grid-cols-4 gap-4" aria-label="榜单目录">
        <button v-for="(chart, index) in charts" :key="`${chart.source}:${chart.id}`" class="mm-card p-3 text-left min-w-0 kpi-card" :class="{ 'ring-2 ring-primary': current?.id === chart.id }" :aria-pressed="current?.id === chart.id" @click="selectChart(chart)">
          <div class="h-28 rounded-md mb-3 flex items-center justify-center overflow-hidden" :style="{ background: gradients[index % gradients.length] }"><img v-if="chart.cover_url && !brokenCovers.has(chart.id)" :src="chart.cover_url" alt="" class="w-full h-full object-cover" loading="lazy" referrerpolicy="no-referrer" @error="brokenCovers.add(chart.id)" /><BarChart3 v-else class="w-9 h-9 text-white/80" /></div>
          <h2 class="font-semibold text-sm break-words">{{ chart.name }}</h2><p class="text-xs text-muted-foreground mt-2">{{ chartSummary(chart) }}</p>
        </button>
      </section>
      <template v-if="current">
        <div class="mm-card p-4 flex flex-wrap justify-between items-center gap-3"><div><h2 class="font-semibold">{{ current.name }}</h2><p class="text-xs text-muted-foreground mt-1">{{ platforms.find(p => p.id === source)?.label }} · {{ chartSummary(current) }}</p></div><div class="flex flex-wrap items-center gap-2"><label class="text-xs text-muted-foreground">解析数量 <select v-model="limit" class="mm-input w-24 ml-1" :disabled="parsing" @change="selectChart(current, true)"><option :value="20">20 首</option><option :value="50">50 首</option><option :value="100">100 首</option><option :value="0">全部</option></select></label><button class="mm-btn btn-ghost" :disabled="parsing" @click="selectChart(current, true)"><RefreshCw class="w-4 h-4" />重新解析</button><button class="mm-btn btn-primary" :disabled="parsing || !tracks.length" @click="downloadTracks = tracks"><Download class="w-4 h-4" />下载榜单</button></div></div>
        <section v-if="parseTask" class="mm-card p-4 space-y-3" aria-label="榜单解析进度" aria-live="polite">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <p class="text-sm font-medium">{{ parseTask.message }}</p>
            <span class="text-xs text-muted-foreground">耗时 {{ elapsed }} 秒<span v-if="parseTask.total != null"> · {{ progressPercent }}%</span></span>
          </div>
          <div v-if="parseTask.total != null" class="h-2 rounded-full bg-muted overflow-hidden" role="progressbar" aria-label="曲目解析进度" :aria-valuenow="progressPercent" :aria-valuemin="0" :aria-valuemax="100">
            <div class="h-full bg-primary transition-[width] duration-300" :style="{ width: `${progressPercent}%` }"></div>
          </div>
          <p class="text-sm">已处理 {{ parseTask.processed }} / {{ parseTask.total ?? '待获取' }} 首 · 可下载 {{ parseTask.available }} 首 · 跳过/失败 {{ parseTask.skipped }} 首</p>
          <p v-if="parseTask.current" class="text-xs text-muted-foreground break-words">当前曲目：{{ parseTask.current }}</p>
          <p v-if="parsing && stalledSeconds >= 30 && !progressError" class="text-xs text-muted-foreground">当前步骤已等待 {{ stalledSeconds }} 秒，正在等待音乐平台响应。</p>
          <p v-if="progressError" class="mm-notice">{{ progressError }}，正在自动重新获取进度…</p>
          <p v-if="parseTask.error" class="text-sm text-destructive break-words">{{ parseTask.error }}</p>
          <div class="flex flex-wrap gap-2">
            <button v-if="parsing && activeStatuses.includes(parseTask.status)" class="mm-btn btn-ghost" :disabled="parseTask.status === 'canceling' || stopping" @click="stopParse">{{ parseTask.status === 'canceling' ? '正在停止…' : '停止解析' }}</button>
            <button v-else-if="['failed', 'canceled'].includes(parseTask.status)" class="mm-btn btn-ghost" @click="selectChart(current, true)">重新解析</button>
          </div>
        </section>
        <p class="mm-notice">按所选数量逐首解析下载信息，切换页面后继续解析并保留结果；QQ 每次最多解析前 100 首，无下载地址或解析失败的曲目计入跳过。</p>
        <TrackList v-model="selected" :tracks="tracks" :loading="parsing" :title="current.name" :loading-text="parseTask?.status === 'success' ? '解析完成，正在加载曲目结果…' : parseTask?.message || '正在创建解析任务…'" :empty-text="parseTask?.status === 'failed' ? '解析失败，请重试' : parseTask?.status === 'canceled' ? '解析已停止' : '没有可下载曲目'" :note="`曲目来自本次解析；无下载地址的曲目可能被过滤。`" @download="downloadTracks = $event" />
      </template>
    </div>
    <TrackDownloadDialog v-if="downloadTracks" :tracks="downloadTracks" @close="downloadTracks = null" />
  </AppShell>
</template>
<script setup>
import { onMounted, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { BarChart3, Download, RefreshCw } from 'lucide-vue-next'
import AppShell from '../components/AppShell.vue'
import TrackList from '../components/TrackList.vue'
import TrackDownloadDialog from '../components/TrackDownloadDialog.vue'
import { useChartsStore, CHART_PLATFORMS, ACTIVE_PARSE_STATUSES } from '../stores/charts'
const chartsStore = useChartsStore()
const { source, charts, loading, current, parsing, tracks, selected, limit, brokenCovers, parseTask, progressError, stopping, elapsed, stalledSeconds, progressPercent } = storeToRefs(chartsStore)
const { chartSummary, loadCharts, changeSource, selectChart, stopParse } = chartsStore
const platforms = CHART_PLATFORMS, activeStatuses = ACTIVE_PARSE_STATUSES
const gradients = ['linear-gradient(135deg,#289c9a,#194545)', 'linear-gradient(135deg,#6366f1,#3730a3)', 'linear-gradient(135deg,#fb923c,#c2410c)', 'linear-gradient(135deg,#ec4899,#9d174d)']
const downloadTracks = ref(null)
onMounted(chartsStore.ensureLoaded)
</script>
