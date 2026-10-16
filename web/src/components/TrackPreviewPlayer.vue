<template>
  <audio ref="audio" preload="metadata" class="hidden"></audio>
  <section v-if="current" class="mm-card p-4 space-y-3 sticky top-20 z-30" aria-label="榜单试听播放器">
    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
      <div class="min-w-0 sm:flex-1" aria-live="polite">
        <p class="text-xs text-muted-foreground">{{ statusText }} · {{ state.index + 1 }} / {{ state.queue.length }} 首</p>
        <h2 class="mt-1 text-sm font-semibold break-words">{{ current.title }}</h2>
        <p class="mt-1 text-xs text-muted-foreground break-words">{{ (current.artists || []).join(' / ') || '未知歌手' }}</p>
      </div>
      <div class="flex items-center gap-2">
        <button type="button" class="mm-btn btn-outline preview-control" aria-label="上一首" title="上一首" :disabled="state.index <= 0" @click="player.playAt(state.index - 1)"><SkipBack class="h-4 w-4" aria-hidden="true" /></button>
        <button type="button" class="mm-btn btn-primary" :aria-label="playing ? '暂停试听' : '播放试听'" @click="player.toggle()"><Pause v-if="playing" class="h-4 w-4" aria-hidden="true" /><Play v-else class="h-4 w-4" aria-hidden="true" />{{ playing ? '暂停' : state.status === 'error' ? '重试' : '播放' }}</button>
        <button type="button" class="mm-btn btn-outline preview-control" aria-label="停止试听" title="停止试听" @click="player.stop()"><Square class="h-4 w-4" aria-hidden="true" /></button>
        <button type="button" class="mm-btn btn-outline preview-control" aria-label="下一首" title="下一首" :disabled="state.index >= state.queue.length - 1" @click="player.playAt(state.index + 1)"><SkipForward class="h-4 w-4" aria-hidden="true" /></button>
      </div>
    </div>
    <div class="flex items-center gap-3">
      <span class="text-xs font-mono text-muted-foreground tabular-nums">{{ formatDuration(seeking ?? state.currentTime) }}</span>
      <input type="range" min="0" :max="state.duration || 0" step="0.1" :value="seeking ?? state.currentTime" :disabled="!state.duration" class="min-w-0 flex-1 h-6 accent-primary cursor-pointer" aria-label="试听进度" :aria-valuetext="`${formatDuration(seeking ?? state.currentTime)} / ${formatDuration(state.duration)}`" @input="seeking = Number($event.target.value)" @change="seek" />
      <span class="text-xs font-mono text-muted-foreground tabular-nums">{{ formatDuration(state.duration) }}</span>
    </div>
    <label class="flex items-center gap-2 text-xs text-muted-foreground">试听队列
      <select class="mm-input flex-1" aria-label="切换试听歌曲" :value="state.index" @change="player.playAt(Number($event.target.value))"><option v-for="(track, index) in state.queue" :key="`${index}:${track.id}`" :value="index">{{ index + 1 }}. {{ track.title }} · {{ (track.artists || []).join(' / ') }}</option></select>
    </label>
    <p v-if="state.error" class="text-sm break-words" style="color: var(--state-error)" role="alert">{{ state.error }}</p>
  </section>
</template>
<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Pause, Play, SkipBack, SkipForward, Square } from 'lucide-vue-next'
import client, { errorMessage } from '../api/client'
import { createPreviewPlayer } from '../utils/preview-player'
import { formatDuration } from '../utils/format'

const emit = defineEmits(['track-change'])
const audio = ref(null), seeking = ref(null)
const state = ref({ queue: [], index: -1, status: 'idle', currentTime: 0, duration: 0, error: '' })
const current = computed(() => state.value.queue[state.value.index])
const playing = computed(() => ['playing', 'loading'].includes(state.value.status))
const statusText = computed(() => ({ loading: '正在加载试听', playing: '正在试听', paused: '已暂停', stopped: '已停止', ended: '试听结束', error: '试听失败' })[state.value.status] || '榜单试听')
let player
onMounted(() => {
  player = createPreviewPlayer({ audio: audio.value, onChange: value => { state.value = value }, resolveUrl: async (track, { signal }) => {
    try {
      const { data } = await client.post(`/tracks/${encodeURIComponent(track.id)}/preview`, null, { signal, timeout: 15000 })
      return data.url
    } catch (error) { throw new Error(errorMessage(error, '获取试听地址失败，请重试')) }
  } })
})
watch([() => current.value?.id, () => state.value.status], ([id, status]) => emit('track-change', ['idle', 'stopped', 'ended'].includes(status) ? null : id), { immediate: true })
watch([() => state.value.index, () => state.value.status], ([index, status], [previousIndex]) => {
  if (index !== previousIndex || ['idle', 'stopped', 'ended', 'error'].includes(status)) seeking.value = null
})
function seek(event) { player.seek(Number(event.target.value)); seeking.value = null }
onBeforeUnmount(() => player?.dispose())
defineExpose({ playTracks: tracks => { seeking.value = null; return player?.playTracks(tracks) } })
</script>
