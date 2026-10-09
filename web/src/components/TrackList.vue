<template>
  <section class="mm-card overflow-hidden">
    <header class="px-4 py-3 border-b border-border flex flex-wrap justify-between gap-2">
      <div><h2 class="text-sm font-semibold">{{ title }}</h2><p class="text-xs text-muted-foreground mt-1">共 {{ tracks.length }} 首 · 已选 {{ selectedTracks.length }} 首</p></div>
      <button class="mm-btn btn-outline" :disabled="loading || !tracks.length" @click="toggleAll">{{ allSelected ? '取消全选' : `一键全选 ${tracks.length} 首` }}</button>
    </header>
    <p v-if="loading" class="p-10 text-center text-muted-foreground" role="status">{{ loadingText }}</p>
    <p v-else-if="!tracks.length" class="p-10 text-center text-sm text-muted-foreground">{{ emptyText }}</p>
    <template v-else>
      <div class="hidden min-[960px]:block overflow-x-auto">
        <table class="mm-table" aria-label="曲目列表">
          <thead><tr><th class="w-10"><input type="checkbox" class="mm-checkbox" aria-label="全选所有曲目" :checked="allSelected" :indeterminate="selectedTracks.length > 0 && !allSelected" @change="toggleAll" /></th><th>#</th><th>标题</th><th>歌手</th><th>专辑</th><th>时长</th><th>音质</th><th>文件大小</th><th>来源</th></tr></thead>
          <tbody><tr v-for="(track, index) in visibleTracks" :key="track.id">
            <td><input type="checkbox" class="mm-checkbox" :aria-label="`选择 ${track.title}`" :checked="modelValue.includes(track.id)" @change="toggle(track.id)" /></td>
            <td class="mono text-muted-foreground">{{ index + 1 }}</td><td class="font-medium">{{ track.title }}</td><td>{{ (track.artists || []).join(' / ') || '—' }}</td><td class="text-muted-foreground">{{ track.album || '—' }}</td><td class="mono">{{ formatDuration(track.duration_s) }}</td>
            <td><span class="mm-badge" :class="qualityInfo(track.quality).tier === 'lossless' ? 'badge-success' : 'badge-pending'">{{ qualityInfo(track.quality).label }}</span></td><td class="mono whitespace-nowrap">{{ formatSize(track.size_bytes) }}</td><td class="text-xs text-muted-foreground">{{ sourceDisplayName(track.source) }}</td>
          </tr></tbody>
        </table>
      </div>
      <div class="min-[960px]:hidden divide-y divide-border">
        <label v-for="track in visibleTracks" :key="track.id" class="flex gap-3 px-4 py-3 cursor-pointer">
          <input type="checkbox" class="mm-checkbox mt-1 shrink-0" :aria-label="`选择 ${track.title}`" :checked="modelValue.includes(track.id)" @change="toggle(track.id)" />
          <div class="min-w-0 flex-1"><p class="font-medium text-sm break-words">{{ track.title }}</p><p class="text-xs text-muted-foreground mt-1 break-words">{{ (track.artists || []).join(' / ') }} · {{ track.album || '未知专辑' }}</p><div class="flex flex-wrap items-center gap-2 mt-2 text-xs text-muted-foreground"><span class="mm-badge" :class="qualityInfo(track.quality).tier === 'lossless' ? 'badge-success' : 'badge-pending'">{{ qualityInfo(track.quality).label }}</span><span>{{ formatDuration(track.duration_s) }}</span><span>大小 {{ formatSize(track.size_bytes) }}</span><span>{{ sourceDisplayName(track.source) }}</span></div></div>
        </label>
      </div>
    </template>
    <footer class="flex flex-wrap items-center justify-between gap-3 p-4 border-t border-border">
      <span class="text-xs text-muted-foreground">显示 {{ visibleTracks.length }} / {{ tracks.length }} 首 · 已选约 {{ selectedSize }}</span>
      <div class="flex flex-wrap gap-2"><button v-if="visibleCount < tracks.length" class="mm-btn btn-ghost" @click="visibleCount += pageSize">加载更多</button><button class="mm-btn btn-primary" :disabled="!selectedTracks.length || loading" @click="emit('download', selectedTracks)"><Download class="w-4 h-4" />下载已选 {{ selectedTracks.length }} 首</button></div>
    </footer>
    <p v-if="note" class="px-4 pb-3 text-xs text-muted-foreground break-words">{{ note }}</p>
  </section>
</template>
<script setup>
import { computed, ref, watch } from 'vue'
import { Download } from 'lucide-vue-next'
import { formatDuration, formatSize, qualityInfo, sourceDisplayName } from '../utils/format'
const props = defineProps({ tracks: { type: Array, default: () => [] }, modelValue: { type: Array, default: () => [] }, title: { type: String, default: '曲目列表' }, loading: Boolean, loadingText: { type: String, default: '正在加载曲目，请稍候…' }, emptyText: { type: String, default: '没有可下载曲目' }, note: String, pageSize: { type: Number, default: 50 } })
const emit = defineEmits(['update:modelValue', 'download'])
const visibleCount = ref(props.pageSize)
watch(() => props.tracks, () => { visibleCount.value = props.pageSize })
const visibleTracks = computed(() => props.tracks.slice(0, visibleCount.value))
const selectedTracks = computed(() => props.tracks.filter(t => props.modelValue.includes(t.id)))
const allSelected = computed(() => props.tracks.length > 0 && props.tracks.every(t => props.modelValue.includes(t.id)))
const selectedSize = computed(() => formatSize(selectedTracks.value.reduce((sum, t) => sum + (t.size_bytes || 0), 0)) + (selectedTracks.value.some(t => !t.size_bytes) ? '（含大小未知曲目）' : ''))
function toggle(id) { emit('update:modelValue', props.modelValue.includes(id) ? props.modelValue.filter(n => n !== id) : [...props.modelValue, id]) }
function toggleAll() { emit('update:modelValue', allSelected.value ? [] : [...new Set(props.tracks.map(t => t.id))]) }
</script>
