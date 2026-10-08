<template>
  <div class="min-h-screen flex flex-col">
    <!-- ===== 顶栏导航 ===== -->
    <nav class="sticky top-0 z-40 bg-card/95 backdrop-blur border-b border-border" aria-label="主导航">
      <div class="mx-auto flex h-16 max-w-[1440px] items-center justify-between gap-4 px-6">
        <div class="flex items-center gap-6 min-w-0">
          <RouterLink to="/" class="flex items-center gap-2 shrink-0">
            <span class="inline-flex items-center justify-center w-8 h-8 rounded-md bg-primary text-primary-foreground">
              <Music4 class="w-4 h-4" />
            </span>
            <span class="font-semibold text-foreground" style="font-size:15px;">MediaMusicService</span>
          </RouterLink>
          <!-- 桌面导航（<960px 收进抽屉） -->
          <div class="hidden min-[960px]:flex items-center gap-1 h-16 no-scrollbar overflow-x-auto">
            <RouterLink
              v-for="item in navItems"
              :key="item.key"
              :to="item.to"
              class="inline-flex items-center gap-1.5 h-16 px-3 text-sm border-b-2 transition-colors whitespace-nowrap"
              :class="isActive(item)
                ? 'text-primary border-primary font-medium'
                : 'text-muted-foreground border-transparent hover:text-foreground'"
              :aria-current="isActive(item) ? 'page' : undefined"
            >
              <component :is="item.icon" class="w-4 h-4" />
              {{ item.label }}
            </RouterLink>
          </div>
        </div>
        <div class="flex items-center gap-2 shrink-0">
          <button class="btn-outline flex h-9 w-9 items-center justify-center rounded-md" :aria-label="theme.isDark ? '切换到浅色模式' : '切换到暗色模式'" @click="theme.toggle()">
            <Sun v-if="theme.isDark" class="w-4 h-4" />
            <Moon v-else class="w-4 h-4" />
          </button>
          <!-- 汉堡按钮（<960px 显示） -->
          <button class="btn-outline flex h-9 w-9 items-center justify-center rounded-md min-[960px]:hidden" aria-label="打开导航菜单" @click="drawerOpen = true">
            <Menu class="w-4 h-4" />
          </button>
        </div>
      </div>
    </nav>

    <!-- ===== 移动端抽屉导航 ===== -->
    <Transition name="fade">
      <div v-if="drawerOpen" class="fixed inset-0 z-50 bg-black/40 min-[960px]:hidden" @click="drawerOpen = false"></div>
    </Transition>
    <Transition name="drawer">
      <aside v-if="drawerOpen" class="fixed inset-y-0 left-0 z-[60] w-64 bg-card border-r border-border flex flex-col min-[960px]:hidden" aria-label="移动端导航">
        <div class="flex items-center justify-between h-16 px-4 border-b border-border">
          <span class="flex items-center gap-2">
            <span class="inline-flex items-center justify-center w-7 h-7 rounded-md bg-primary text-primary-foreground">
              <Music4 class="w-3.5 h-3.5" />
            </span>
            <span class="font-semibold text-foreground text-sm">MediaMusicService</span>
          </span>
          <button class="text-muted-foreground hover:text-foreground transition-colors" aria-label="关闭导航菜单" @click="drawerOpen = false">
            <X class="w-5 h-5" />
          </button>
        </div>
        <div class="flex-1 overflow-y-auto py-2">
          <RouterLink
            v-for="item in navItems"
            :key="item.key"
            :to="item.to"
            class="flex items-center gap-3 px-4 py-2.5 text-sm transition-colors"
            :class="isActive(item) ? 'text-primary font-medium bg-muted' : 'text-muted-foreground hover:text-foreground hover:bg-muted'"
            @click="drawerOpen = false"
          >
            <component :is="item.icon" class="w-4 h-4" />
            {{ item.label }}
          </RouterLink>
        </div>
      </aside>
    </Transition>

    <main class="flex-1 w-full max-w-[1440px] mx-auto px-6 py-6">
      <slot />
    </main>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRoute } from 'vue-router'
import {
  Music4, LayoutDashboard, Search, ListMusic, BarChart3, Disc3,
  DownloadCloud, Library, Server, Settings, Menu, X, Sun, Moon,
} from 'lucide-vue-next'
import { useThemeStore } from '../stores/theme'

const theme = useThemeStore()
const route = useRoute()
const drawerOpen = ref(false)

const navItems = [
  { key: 'dashboard', label: '概览', to: '/', icon: LayoutDashboard },
  { key: 'search', label: '搜索', to: '/search', icon: Search },
  { key: 'playlists', label: '歌单', to: '/playlists', icon: ListMusic },
  { key: 'charts', label: '榜单', to: '/charts', icon: BarChart3 },
  { key: 'albums', label: '专辑', to: '/albums', icon: Disc3 },
  { key: 'tasks', label: '任务', to: '/tasks', icon: DownloadCloud },
  { key: 'library', label: '媒体库', to: '/library', icon: Library },
  { key: 'fnos', label: '飞牛', to: '/fnos', icon: Server },
  { key: 'settings', label: '设置', to: '/settings', icon: Settings },
]

function isActive(item) {
  return route.name === item.key
}
</script>
