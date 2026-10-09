<template>
  <section class="space-y-4">
    <div class="flex flex-wrap justify-between gap-2"><h3 class="text-sm font-semibold">匹配清单摘要</h3><span class="text-xs text-muted-foreground">成功 {{ manifest.summary?.ok || 0 }} / {{ manifest.summary?.total || entries.length }}</span></div>
    <div class="grid grid-cols-2 min-[960px]:grid-cols-5 gap-3">
      <div v-for="bucket in buckets" :key="bucket.label"><div class="flex justify-between text-xs mb-2"><span>{{ bucket.label }}</span><span>{{ bucket.count }}</span></div><div class="h-2 rounded-full bg-muted overflow-hidden"><div class="h-full rounded-full" :style="{ width: `${entries.length ? bucket.count / entries.length * 100 : 0}%`, background: bucket.color }"></div></div></div>
    </div>
    <ul class="divide-y divide-border">
      <li v-for="entry in entries" :key="`${entry.disc}-${entry.track}`" class="py-3 flex items-start justify-between gap-3">
        <div class="min-w-0"><p class="text-sm"><span class="mono text-muted-foreground">{{ entry.disc }}-{{ String(entry.track).padStart(2, '0') }}</span> {{ entry.title }}</p><p class="mt-1 text-xs text-muted-foreground break-words">{{ sourceDisplayName(entry.match?.source) }} · {{ entry.match?.ext || '—' }} · {{ entry.match?.score != null ? `匹配分 ${entry.match.score}` : entry.match?.source === 'singles' ? '本地复用，无评分' : '无匹配分' }}</p><p v-if="entry.error" class="text-xs mt-1 text-[var(--state-error)] break-words">{{ entry.error }}</p><p v-if="entry.match?.oversized_relaxed" class="text-xs mt-1 text-[var(--state-warning)]">为保证专辑完整性，服务已放宽此曲体积上限。</p></div>
        <div class="flex flex-col items-end gap-2 shrink-0"><span class="mm-badge" :class="entry.status === 'ok' ? 'badge-success' : 'badge-failed'">{{ entry.status === 'ok' ? '已下载' : entry.status === 'unmatched' ? '未匹配' : '失败' }}</span><span class="text-xs text-muted-foreground whitespace-nowrap">大小 {{ formatSize(entry.size_bytes) }}</span><RouterLink v-if="entry.status !== 'ok'" class="text-xs text-primary" :to="{ path: '/search', query: { keyword: `${(entry.artists || []).join(' ')} ${entry.title}`.trim() } }">重搜此曲</RouterLink></div>
      </li>
    </ul>
  </section>
</template>
<script setup>
import { computed } from 'vue'
import { formatSize, sourceDisplayName } from '../utils/format'
const props = defineProps({ manifest: { type: Object, required: true } })
const entries = computed(() => props.manifest.tracks || [])
const buckets = computed(() => [
  { label: '≥ 0.9', color: 'var(--state-success)', count: entries.value.filter(e => e.status === 'ok' && e.match?.score >= 0.9).length },
  { label: '0.7–0.9', color: 'var(--state-info)', count: entries.value.filter(e => e.status === 'ok' && e.match?.score >= 0.7 && e.match.score < 0.9).length },
  { label: '< 0.7', color: 'var(--state-warning)', count: entries.value.filter(e => e.status === 'ok' && e.match?.score != null && e.match.score < 0.7).length },
  { label: '复用 / 无评分', color: 'var(--mm-primary)', count: entries.value.filter(e => e.status === 'ok' && e.match?.score == null).length },
  { label: '失败 / 未匹配', color: 'var(--state-error)', count: entries.value.filter(e => e.status !== 'ok').length },
])
</script>
