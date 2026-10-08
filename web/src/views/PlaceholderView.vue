<template>
  <AppShell>
    <div class="flex flex-col items-center justify-center py-24 text-center">
      <span class="inline-flex items-center justify-center w-14 h-14 rounded-lg mb-4" style="background: var(--mm-brand-50); color: var(--mm-primary);">
        <component :is="icon" class="w-7 h-7" />
      </span>
      <h1 class="text-2xl font-semibold text-foreground">{{ route.meta.title }}</h1>
      <p class="mt-2 text-sm text-muted-foreground max-w-md">{{ route.meta.desc }}</p>
      <span class="mm-badge badge-source mt-4" style="padding: 4px 14px;">{{ route.meta.phase }} 交付</span>
    </div>
  </AppShell>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { ListMusic, BarChart3, Disc3, Library, Server, Settings } from 'lucide-vue-next'
import AppShell from '../components/AppShell.vue'

const route = useRoute()

const ICONS = {
  playlists: ListMusic,
  charts: BarChart3,
  albums: Disc3,
  library: Library,
  fnos: Server,
  settings: Settings,
}

const icon = computed(() => ICONS[route.name] || Settings)
</script>
