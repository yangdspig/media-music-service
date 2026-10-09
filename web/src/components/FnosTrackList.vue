<template>
  <section class="mm-card overflow-hidden min-w-0">
    <header class="p-4 border-b border-border flex flex-wrap justify-between gap-3"><h2 class="text-sm font-semibold">{{ title }} · {{ tracks.length }} 首</h2><label v-if="selectable" class="flex gap-2 items-center text-xs"><input type="checkbox" class="mm-checkbox" :checked="allSelected" :disabled="!tracks.length" @change="emit('update:modelValue', allSelected ? [] : tracks.filter(t => t.guid).map(t => t.guid))" />选择全部</label></header>
    <p v-if="loading" class="p-6 text-sm text-muted-foreground" role="status">正在读取曲目…</p><p v-else-if="!tracks.length" class="p-6 text-sm text-muted-foreground">{{ emptyText }}</p>
    <template v-else>
      <div class="hidden min-[960px]:block"><table class="w-full text-left text-sm"><thead class="bg-muted text-xs text-muted-foreground"><tr><th v-if="selectable" class="w-10 p-3"><span class="sr-only">选择曲目</span></th><th class="p-3">曲名</th><th class="p-3">艺人</th><th class="p-3">专辑</th><th class="p-3 w-20">时长</th></tr></thead><tbody class="divide-y divide-border"><tr v-for="(track, index) in visible" :key="track.guid || index" class="table-row-hover"><td v-if="selectable" class="p-3"><input type="checkbox" class="mm-checkbox" :checked="modelValue.includes(track.guid)" :disabled="!track.guid" :aria-label="`选择 ${track.title}`" @change="toggle(track.guid)" /></td><td class="p-3 break-words font-medium">{{ track.title || '未知曲名' }}</td><td class="p-3 break-words">{{ (track.artists || []).join(' / ') || '—' }}</td><td class="p-3 break-words text-muted-foreground">{{ track.album || '—' }}</td><td class="p-3 mono">{{ formatDuration(track.duration == null ? null : track.duration / 1000) }}</td></tr></tbody></table></div>
      <div class="min-[960px]:hidden divide-y divide-border"><div v-for="(track, index) in visible" :key="track.guid || index" class="p-4 flex gap-3"><input v-if="selectable" type="checkbox" class="mm-checkbox mt-1 shrink-0" :checked="modelValue.includes(track.guid)" :disabled="!track.guid" :aria-label="`选择 ${track.title}`" @change="toggle(track.guid)" /><div class="min-w-0 flex-1"><p class="text-sm font-medium break-words">{{ track.title || '未知曲名' }}</p><p class="text-xs text-muted-foreground break-words mt-1">{{ (track.artists || []).join(' / ') || '未知艺人' }} · {{ track.album || '未知专辑' }}</p><p class="mono text-muted-foreground mt-2">{{ formatDuration(track.duration == null ? null : track.duration / 1000) }}</p></div></div></div>
      <footer v-if="tracks.length > limit" class="p-3 border-t border-border flex justify-between items-center"><span class="text-xs text-muted-foreground">显示 {{ visible.length }} / {{ tracks.length }} 首</span><button class="mm-btn btn-ghost" @click="limit += 50">显示更多</button></footer>
    </template>
  </section>
</template>
<script setup>
import { computed, ref, watch } from 'vue'
import { formatDuration } from '../utils/format'
const props = defineProps({ title: { type: String, default: '曲目' }, tracks: { type: Array, default: () => [] }, loading: Boolean, selectable: Boolean, modelValue: { type: Array, default: () => [] }, emptyText: { type: String, default: '暂无曲目' } })
const emit = defineEmits(['update:modelValue'])
const limit = ref(50), visible = computed(() => props.tracks.slice(0, limit.value)), allSelected = computed(() => props.tracks.length > 0 && props.tracks.filter(t => t.guid).every(t => props.modelValue.includes(t.guid)))
watch(() => props.tracks, () => { limit.value = 50 })
function toggle(guid) { emit('update:modelValue', props.modelValue.includes(guid) ? props.modelValue.filter(g => g !== guid) : [...props.modelValue, guid]) }
</script>
