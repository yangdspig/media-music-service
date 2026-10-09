<template>
  <AppShell>
    <div class="space-y-5">
      <section class="mm-card p-5 space-y-4">
        <div><h1 class="text-xl font-semibold">歌单解析</h1><p class="text-sm text-muted-foreground mt-1">粘贴歌单链接，解析后选择曲目并提交下载。</p></div>
        <div class="mm-notice flex gap-2"><AlertTriangle class="w-4 h-4 shrink-0 mt-0.5" /><p>大歌单解析可能需要 1–10 分钟。解析期间请保持页面打开，避免重复提交。</p></div>
        <form class="flex flex-wrap gap-3" @submit.prevent="parse">
          <label class="flex-1 min-w-0 w-full sm:min-w-60"><span class="sr-only">歌单链接</span><input v-model.trim="url" class="mm-input h-10" type="url" required placeholder="https://music.163.com/playlist?id=…" :disabled="parsing" /></label>
          <label><span class="sr-only">歌单来源</span><select v-model="source" class="mm-input h-10" :disabled="parsing"><option value="">自动识别来源</option><option v-for="item in sources" :key="item.name" :value="item.name">{{ sourceDisplayName(item.name) }}</option></select></label>
          <button class="mm-btn btn-primary h-10" :disabled="parsing"><Loader2 v-if="parsing" class="w-4 h-4 animate-spin" /><ListMusic v-else class="w-4 h-4" />{{ parsing ? `解析中 · ${elapsed}s` : '解析歌单' }}</button>
        </form>
        <p class="text-xs text-muted-foreground">来源列表仅显示支持歌单解析的可用音源。</p>
      </section>
      <section v-if="parsed" class="mm-card p-5 flex items-start gap-4">
        <span class="w-16 h-16 rounded-lg bg-primary/10 text-primary flex items-center justify-center shrink-0"><ListMusic class="w-8 h-8" /></span>
        <div class="min-w-0"><h2 class="font-semibold">歌单解析结果</h2><p class="text-xs text-muted-foreground mt-2 break-all">{{ parsedUrl }}</p><p class="text-sm mt-3">可下载 {{ tracks.length }} 首 · 解析耗时 {{ duration }} 秒</p></div>
      </section>
      <TrackList v-if="parsed || parsing" v-model="selected" :tracks="tracks" :loading="parsing" loading-text="正在逐曲解析下载信息，请耐心等待…" :note="parsed ? '列表为已解析的可下载曲目；无下载地址的曲目可能被平台过滤。' : ''" @download="downloadTracks = $event" />
    </div>
    <TrackDownloadDialog v-if="downloadTracks" :tracks="downloadTracks" @close="downloadTracks = null" />
  </AppShell>
</template>
<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { AlertTriangle, ListMusic, Loader2 } from 'lucide-vue-next'
import AppShell from '../components/AppShell.vue'
import TrackList from '../components/TrackList.vue'
import TrackDownloadDialog from '../components/TrackDownloadDialog.vue'
import client, { errorMessage } from '../api/client'
import { useToastStore } from '../stores/toast'
import { sourceDisplayName } from '../utils/format'
const toast = useToastStore()
const url = ref(''), source = ref(''), sources = ref([]), tracks = ref([]), selected = ref([]), downloadTracks = ref(null)
const parsing = ref(false), parsed = ref(false), parsedUrl = ref(''), elapsed = ref(0), duration = ref(0)
const controller = new AbortController()
let timer
onMounted(async () => { try { sources.value = (await client.get('/sources', { signal: controller.signal })).data.filter(s => s.available && s.supports_playlist) } catch (e) { if (!controller.signal.aborted) toast.show(errorMessage(e, '获取音源失败'), 'error') } })
async function parse() {
  if (parsing.value || !url.value) return
  parsing.value = true; parsed.value = false; tracks.value = []; selected.value = []; elapsed.value = 0
  const started = Date.now(), submittedUrl = url.value
  timer = setInterval(() => { elapsed.value = Math.floor((Date.now() - started) / 1000) }, 1000)
  try {
    const params = { url: submittedUrl }
    if (source.value) params.source = source.value
    tracks.value = (await client.get('/playlist', { params, timeout: 600000, signal: controller.signal })).data
    parsedUrl.value = submittedUrl; parsed.value = true; duration.value = ((Date.now() - started) / 1000).toFixed(1)
    toast.show(`解析完成，可下载 ${tracks.value.length} 首`, 'success')
  } catch (e) { if (!controller.signal.aborted) toast.show(errorMessage(e, '歌单解析失败'), 'error') }
  finally { clearInterval(timer); parsing.value = false }
}
onBeforeUnmount(() => { controller.abort(); clearInterval(timer) })
</script>
