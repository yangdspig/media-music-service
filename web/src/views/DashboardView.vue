<template>
  <AppShell>
    <!-- ===== 页头 ===== -->
    <div class="mb-6 flex items-center justify-between">
      <div>
        <h1 class="text-2xl font-semibold text-foreground">概览</h1>
        <p class="mt-1 text-sm text-muted-foreground">系统运行状态与最近任务总览</p>
      </div>
      <button class="btn-primary btn-lift flex items-center gap-2 rounded-md px-4 py-2 text-sm font-medium" :disabled="loading" @click="loadAll">
        <RefreshCw class="h-4 w-4" :class="{ 'animate-spin': loading }" />
        刷新状态
      </button>
    </div>

    <!-- ===== 统计指标 ===== -->
    <section class="mb-6 grid grid-cols-2 gap-4 lg:grid-cols-4" aria-label="统计指标">
      <div class="kpi-card rounded-lg border border-border bg-card p-5 shadow-sm">
        <div class="flex items-center justify-between">
          <span class="text-sm text-muted-foreground">可用源数量</span>
          <RadioTower class="h-4 w-4 text-muted-foreground" />
        </div>
        <div class="mt-2 flex items-baseline gap-2">
          <span class="text-2xl font-semibold text-foreground">{{ availableCount }}</span>
          <span class="text-sm text-muted-foreground">/ {{ sources.length }}</span>
        </div>
        <p class="mt-1 text-xs text-muted-foreground">
          {{ sources.length ? `覆盖 ${Math.round((availableCount / sources.length) * 100)}% 的音乐源` : '暂无数据' }}
        </p>
      </div>
      <div class="kpi-card rounded-lg border border-border bg-card p-5 shadow-sm">
        <div class="flex items-center justify-between">
          <span class="text-sm text-muted-foreground">进行中任务</span>
          <Loader2 class="h-4 w-4 text-muted-foreground" />
        </div>
        <div class="mt-2 flex items-baseline gap-2">
          <span class="text-2xl font-semibold text-foreground">{{ activeCount }}</span>
          <span class="text-sm text-muted-foreground">个任务</span>
        </div>
        <p class="mt-1 text-xs text-muted-foreground">{{ runningCount }} 个下载中，{{ pendingCount }} 个排队</p>
      </div>
      <div class="kpi-card rounded-lg border border-border bg-card p-5 shadow-sm">
        <div class="flex items-center justify-between">
          <span class="text-sm text-muted-foreground">今日下载</span>
          <Download class="h-4 w-4 text-muted-foreground" />
        </div>
        <div class="mt-2 flex items-baseline gap-2">
          <span class="text-2xl font-semibold text-foreground">{{ todayTracks }}</span>
          <span class="text-sm text-muted-foreground">首</span>
        </div>
        <p class="mt-1 text-xs text-muted-foreground">今日共 {{ todaySuccessCount }} 个任务完成</p>
      </div>
      <div class="kpi-card rounded-lg border border-border bg-card p-5 shadow-sm">
        <div class="flex items-center justify-between">
          <span class="text-sm text-muted-foreground">媒体库占用</span>
          <HardDrive class="h-4 w-4 text-muted-foreground" />
        </div>
        <template v-if="systemStatus === null">
          <div class="mt-2 flex items-baseline gap-2">
            <span class="text-2xl font-semibold text-muted-foreground">—</span>
          </div>
          <p class="mt-1 text-xs text-muted-foreground">接口未就绪</p>
        </template>
        <template v-else-if="libraryUsage">
          <div class="mt-2 flex items-baseline gap-2">
            <span class="text-2xl font-semibold text-foreground">{{ libraryUsage.usedText }}</span>
          </div>
          <p class="mt-1 text-xs text-muted-foreground">{{ libraryUsage.detail }}</p>
        </template>
        <template v-else>
          <div class="mt-2 flex items-baseline gap-2">
            <span class="text-2xl font-semibold text-muted-foreground">—</span>
          </div>
          <p class="mt-1 text-xs text-muted-foreground">暂无占用数据</p>
        </template>
      </div>
    </section>

    <!-- ===== 源状态 ===== -->
    <section class="mb-6 rounded-lg border border-border bg-card p-5 shadow-sm" aria-label="源状态">
      <div class="mb-4 flex items-center justify-between">
        <h2 class="text-lg font-medium text-foreground">源状态</h2>
        <button class="btn-secondary flex items-center gap-2 rounded-md px-3 py-1.5 text-sm" :disabled="sourcesLoading" @click="loadSources">
          <RefreshCw class="h-3.5 w-3.5" :class="{ 'animate-spin': sourcesLoading }" />
          刷新状态
        </button>
      </div>
      <div v-if="sourcesLoading && !sources.length" class="py-8 text-center text-sm text-muted-foreground">加载中...</div>
      <div v-else-if="!sources.length" class="py-8 text-center text-sm text-muted-foreground">未能获取源列表</div>
      <div v-else class="grid grid-cols-2 gap-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6">
        <div
          v-for="src in sources"
          :key="src.name"
          class="source-chip flex items-center gap-2 rounded-md border border-border px-3 py-2"
          :title="src.available ? src.name : (src.note || '不可用')"
        >
          <span class="status-dot" :class="src.available ? 'status-dot--on' : 'status-dot--off'"></span>
          <span class="truncate text-sm" :class="{ 'text-muted-foreground': !src.available }">{{ sourceDisplayName(src.name) }}</span>
        </div>
      </div>
      <p v-if="sources.length" class="mt-4 text-xs text-muted-foreground">
        共 {{ sources.length }} 个源，{{ availableCount }} 个可用，{{ sources.length - availableCount }} 个不可用。不可用源已自动降级或移除。
      </p>
    </section>

    <!-- ===== 最近任务 ===== -->
    <section class="mb-6 rounded-lg border border-border bg-card p-5 shadow-sm" aria-label="最近任务">
      <div class="mb-4 flex items-center justify-between">
        <h2 class="text-lg font-medium text-foreground">最近任务</h2>
        <RouterLink to="/tasks" class="text-sm text-primary hover:underline">查看全部</RouterLink>
      </div>
      <div v-if="tasksLoading && !recentTasks.length" class="py-8 text-center text-sm text-muted-foreground">加载中...</div>
      <div v-else-if="!recentTasks.length" class="py-8 text-center text-sm text-muted-foreground">暂无任务，去搜索页提交第一个下载吧</div>
      <div v-else class="overflow-x-auto">
        <table class="w-full text-left text-sm">
          <thead>
            <tr class="border-b border-border text-muted-foreground">
              <th class="pb-3 pr-4 font-medium">任务ID</th>
              <th class="pb-3 pr-4 font-medium">类型</th>
              <th class="pb-3 pr-4 font-medium">内容</th>
              <th class="pb-3 pr-4 font-medium">状态</th>
              <th class="pb-3 pr-4 font-medium">进度</th>
              <th class="pb-3 font-medium">保存目录</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="task in recentTasks" :key="task.task_id" class="table-row-hover border-b border-border last:border-b-0">
              <td class="py-3 pr-4 font-mono text-xs">{{ task.task_id }}</td>
              <td class="py-3 pr-4">{{ isAlbumTask(task) ? '专辑下载' : '单曲下载' }}</td>
              <td class="py-3 pr-4">{{ taskDisplayName(task) }}</td>
              <td class="py-3 pr-4">
                <span class="task-status" :class="`task-status--${task.status}`">{{ TASK_STATUS_TEXT[task.status] || task.status }}</span>
              </td>
              <td class="py-3 pr-4">
                <div class="flex items-center gap-2">
                  <div class="progress-track w-24">
                    <div
                      class="progress-fill"
                      :style="{ width: progressPercent(task) + '%', background: task.status === 'success' ? 'var(--state-success)' : undefined }"
                    ></div>
                  </div>
                  <span class="text-xs text-muted-foreground">{{ progressPercent(task) }}%</span>
                </div>
              </td>
              <td class="py-3 text-muted-foreground font-mono text-xs">{{ task.save_dir || '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <!-- ===== 快捷操作 ===== -->
    <section class="rounded-lg border border-border bg-card p-5 shadow-sm" aria-label="快捷操作">
      <h2 class="mb-4 text-lg font-medium text-foreground">快捷操作</h2>
      <div class="flex flex-wrap gap-3">
        <RouterLink to="/search" class="btn-primary btn-lift flex items-center gap-2 rounded-md px-4 py-2 text-sm font-medium">
          <Search class="h-4 w-4" />
          快速搜索
        </RouterLink>
        <RouterLink to="/playlists" class="btn-secondary flex items-center gap-2 rounded-md px-4 py-2 text-sm font-medium">
          <ListMusic class="h-4 w-4" />
          解析歌单
        </RouterLink>
        <RouterLink to="/charts" class="btn-secondary flex items-center gap-2 rounded-md px-4 py-2 text-sm font-medium">
          <BarChart3 class="h-4 w-4" />
          查看榜单
        </RouterLink>
        <RouterLink to="/settings" class="btn-secondary flex items-center gap-2 rounded-md px-4 py-2 text-sm font-medium">
          <Settings class="h-4 w-4" />
          系统设置
        </RouterLink>
      </div>
    </section>
  </AppShell>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import {
  RefreshCw, RadioTower, Loader2, Download, HardDrive,
  Search, ListMusic, BarChart3, Settings,
} from 'lucide-vue-next'
import AppShell from '../components/AppShell.vue'
import client, { errorMessage } from '../api/client'
import { useToastStore } from '../stores/toast'
import { sourceDisplayName, taskDisplayName, isAlbumTask, TASK_STATUS_TEXT } from '../utils/format'

const toast = useToastStore()

const sources = ref([])
const tasks = ref([])
const recentTasks = ref([])
const systemStatus = ref(undefined) // undefined=未加载 null=接口未就绪
const loading = ref(false)
const sourcesLoading = ref(false)
const tasksLoading = ref(false)

const availableCount = computed(() => sources.value.filter((s) => s.available).length)
const runningCount = computed(() => tasks.value.filter((t) => t.status === 'running').length)
const pendingCount = computed(() => tasks.value.filter((t) => t.status === 'pending').length)
const activeCount = computed(() => runningCount.value + pendingCount.value)

// 内存队列无创建时间，今日统计以当前列表近似
const todaySuccessCount = computed(() => tasks.value.filter((t) => t.status === 'success').length)
const todayTracks = computed(() =>
  tasks.value.filter((t) => t.status === 'success').reduce((sum, t) => sum + (t.completed || 0), 0),
)

const libraryUsage = computed(() => {
  const s = systemStatus.value
  if (!s || typeof s !== 'object') return null
  // 后端 /system/status 的 download_dir 段（单项失败时该段为 {error}）
  const dir = s.download_dir
  if (!dir || dir.error || dir.size_gb == null) return null
  const usedText = dir.size_gb >= 1 ? `${dir.size_gb.toFixed(1)} GB` : `${Math.round(dir.size_gb * 1024)} MB`
  if (dir.max_size_gb) {
    const pct = Math.round((dir.size_gb / dir.max_size_gb) * 100)
    return { usedText, detail: `已用 ${pct}%，上限 ${dir.max_size_gb} GB` }
  }
  return { usedText, detail: '下载目录占用' }
})

function progressPercent(task) {
  if (!task.total) return task.status === 'success' ? 100 : 0
  return Math.min(100, Math.round(((task.completed + task.failed) / task.total) * 100))
}

async function loadSources() {
  sourcesLoading.value = true
  try {
    const { data } = await client.get('/sources')
    sources.value = data
  } catch (e) {
    toast.show(errorMessage(e, '获取源列表失败'), 'error')
  } finally {
    sourcesLoading.value = false
  }
}

async function loadTasks() {
  tasksLoading.value = true
  try {
    const responses = await Promise.allSettled([
      client.get('/downloads', { params: { limit: 8 } }),
      client.get('/history', { params: { limit: 8, order_by: 'completed_at' } }),
    ])
    if (responses[0].status === 'fulfilled') tasks.value = responses[0].value.data
    else toast.show(errorMessage(responses[0].reason, '获取任务列表失败'), 'error')
    if (responses[1].status === 'fulfilled') {
      const liveTasks = new Map(tasks.value.map((task) => [task.task_id, task]))
      recentTasks.value = responses[1].value.data.map((task) => ({ ...liveTasks.get(task.task_id), ...task }))
    } else {
      toast.show(errorMessage(responses[1].reason, '获取最近任务失败'), 'error')
    }
  } catch (e) {
    toast.show(errorMessage(e, '获取任务列表失败'), 'error')
  } finally {
    tasksLoading.value = false
  }
}

async function loadSystemStatus() {
  try {
    const { data } = await client.get('/system/status')
    systemStatus.value = data
  } catch (e) {
    // 后端并行开发中：404 或其他错误时媒体库占用卡优雅降级
    systemStatus.value = null
  }
}

async function loadAll() {
  loading.value = true
  await Promise.all([loadSources(), loadTasks(), loadSystemStatus()])
  loading.value = false
}

onMounted(loadAll)
</script>
