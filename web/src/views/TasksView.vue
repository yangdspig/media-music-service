<template>
  <AppShell>
    <!-- ===== 页头 ===== -->
    <div class="flex items-center justify-between mb-4">
      <div>
        <h1 class="text-xl font-semibold text-foreground">任务中心</h1>
        <p class="text-sm text-muted-foreground mt-1">管理下载任务：查看进行中任务、浏览历史记录、取消等待中的任务。</p>
      </div>
      <div class="flex items-center gap-2">
        <button type="button" class="mm-btn btn-primary" :disabled="refreshing" @click="refresh">
          <RefreshCw class="w-4 h-4" :class="{ 'animate-spin': refreshing }" />刷新
        </button>
      </div>
    </div>

    <!-- ===== 统计条 ===== -->
    <div class="grid gap-3 mb-4 grid-cols-2 lg:grid-cols-4">
      <div class="mm-card p-4 flex items-center gap-3">
        <span class="inline-flex items-center justify-center w-9 h-9 rounded-md shrink-0" style="background:#eff6ff;color:#1d4ed8;"><Loader class="w-4 h-4" /></span>
        <div>
          <div class="text-xs text-muted-foreground">进行中</div>
          <div class="text-lg font-semibold text-foreground">{{ runningTasks.length }}</div>
        </div>
      </div>
      <div class="mm-card p-4 flex items-center gap-3">
        <span class="inline-flex items-center justify-center w-9 h-9 rounded-md shrink-0" style="background:var(--mm-gray-100);color:var(--mm-gray-600);"><Clock class="w-4 h-4" /></span>
        <div>
          <div class="text-xs text-muted-foreground">等待中</div>
          <div class="text-lg font-semibold text-foreground">{{ pendingTasks.length }}</div>
        </div>
      </div>
      <div class="mm-card p-4 flex items-center gap-3">
        <span class="inline-flex items-center justify-center w-9 h-9 rounded-md shrink-0" style="background:#ecfdf5;color:#047857;"><CheckCircle2 class="w-4 h-4" /></span>
        <div>
          <div class="text-xs text-muted-foreground">今日成功</div>
          <div class="text-lg font-semibold text-foreground">{{ todaySuccess }}</div>
        </div>
      </div>
      <div class="mm-card p-4 flex items-center gap-3">
        <span class="inline-flex items-center justify-center w-9 h-9 rounded-md shrink-0" style="background:#fef2f2;color:#b91c1c;"><XCircle class="w-4 h-4" /></span>
        <div>
          <div class="text-xs text-muted-foreground">今日失败</div>
          <div class="text-lg font-semibold text-foreground">{{ todayFailed }}</div>
        </div>
      </div>
    </div>

    <!-- ===== 页签 + 表格 ===== -->
    <section class="mm-card">
      <div class="flex items-center justify-between px-4" style="padding-top:4px;">
        <div class="tab-bar" style="flex:1;" role="tablist" aria-label="任务列表切换">
          <button type="button" role="tab" :aria-selected="tab === 'active'" class="tab-item" :class="{ active: tab === 'active' }" @click="switchTab('active')">
            <Activity class="w-4 h-4" />进行中
            <span class="mm-badge badge-running" style="padding:0 8px;">{{ activeTasks.length }}</span>
          </button>
          <button type="button" role="tab" :aria-selected="tab === 'history'" class="tab-item" :class="{ active: tab === 'history' }" @click="switchTab('history')">
            <History class="w-4 h-4" />历史记录
          </button>
        </div>
        <span class="text-xs text-muted-foreground hidden sm:flex items-center gap-1" style="padding-right:4px;">
          <Database class="w-3.5 h-3.5" />
          <span>{{ tab === 'active' ? '进行中任务来自内存队列，服务重启后丢失' : '历史记录来自 SQLite 持久化存储' }}</span>
        </span>
      </div>

      <!-- 进行中 -->
      <div v-show="tab === 'active'" class="overflow-x-auto">
        <table class="mm-table" aria-label="下载任务列表">
          <thead>
            <tr>
              <th>任务 ID</th>
              <th>名称</th>
              <th>状态</th>
              <th>进度</th>
              <th>当前来源</th>
              <th>保存目录</th>
              <th style="text-align:right;">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="!activeTasks.length">
              <td colspan="7" class="text-center text-muted-foreground" style="padding:32px 14px;">暂无进行中任务</td>
            </tr>
            <tr v-for="task in activeTasks" :key="task.task_id">
              <td class="mono text-muted-foreground">{{ task.task_id }}</td>
              <td class="font-medium text-foreground">{{ taskDisplayName(task) }}</td>
              <td>
                <span class="mm-badge" :class="`badge-${task.status}`">
                  <Loader2 v-if="task.status === 'running'" class="w-3 h-3 animate-spin" />
                  <Clock v-else-if="task.status === 'pending'" class="w-3 h-3" />
                  <Check v-else-if="task.status === 'success'" class="w-3 h-3" />
                  <X v-else-if="task.status === 'failed'" class="w-3 h-3" />
                  <MinusCircle v-else class="w-3 h-3" />
                  {{ TASK_STATUS_TEXT[task.status] || task.status }}
                </span>
              </td>
              <td>
                <div class="flex items-center gap-2">
                  <div class="progress-track">
                    <div class="progress-fill" :style="{ width: progressPercent(task) + '%', background: task.status === 'success' ? 'var(--state-success)' : undefined }"></div>
                  </div>
                  <span class="mono text-muted-foreground">{{ task.completed + task.failed }}/{{ task.total }}</span>
                </div>
              </td>
              <td>
                <span v-if="task.current" class="mm-badge badge-source">{{ task.current }}</span>
                <span v-else class="text-muted-foreground">—</span>
              </td>
              <td class="mono text-muted-foreground">{{ task.save_dir || '—' }}</td>
              <td style="text-align:right;">
                <div class="inline-flex gap-2">
                  <button
                    v-if="task.status === 'pending'"
                    type="button"
                    class="mm-btn btn-danger-ghost"
                    :disabled="cancelingId === task.task_id"
                    @click="cancelTask(task)"
                  ><Ban class="w-3.5 h-3.5" />取消</button>
                  <button
                    v-else-if="task.status === 'running'"
                    type="button"
                    class="mm-btn btn-danger-ghost"
                    disabled
                    title="运行中任务不可中断"
                  ><Ban class="w-3.5 h-3.5" />取消</button>
                  <button
                    v-if="task.status === 'success' && task.manifest_path"
                    type="button"
                    class="mm-btn btn-ghost"
                    @click="openArchive(task)"
                  ><Archive class="w-3.5 h-3.5" />去归档</button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- 历史记录 -->
      <div v-show="tab === 'history'" class="overflow-x-auto">
        <table class="mm-table" aria-label="历史任务列表">
          <thead>
            <tr>
              <th>任务 ID</th>
              <th>名称</th>
              <th>最终状态</th>
              <th>完成数</th>
              <th>耗时</th>
              <th>结束时间</th>
              <th>保存目录</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="historyLoading && !history.length">
              <td colspan="7" class="text-center text-muted-foreground" style="padding:32px 14px;">加载中...</td>
            </tr>
            <tr v-else-if="!history.length">
              <td colspan="7" class="text-center text-muted-foreground" style="padding:32px 14px;">暂无历史记录</td>
            </tr>
            <tr v-for="row in history" :key="row.task_id">
              <td class="mono text-muted-foreground">{{ row.task_id }}</td>
              <td class="font-medium text-foreground">{{ taskDisplayName(row) }}</td>
              <td>
                <span class="mm-badge" :class="`badge-${row.status}`">
                  <Check v-if="row.status === 'success'" class="w-3 h-3" />
                  <X v-else-if="row.status === 'failed'" class="w-3 h-3" />
                  <MinusCircle v-else-if="row.status === 'canceled'" class="w-3 h-3" />
                  <Clock v-else class="w-3 h-3" />
                  {{ TASK_STATUS_TEXT[row.status] || row.status }}
                </span>
              </td>
              <td class="mono text-muted-foreground">{{ row.completed }}/{{ row.total }}</td>
              <td class="text-muted-foreground">{{ durationText(row) }}</td>
              <td class="text-muted-foreground">{{ timeText(row.updated_at) }}</td>
              <td class="mono text-muted-foreground">{{ row.save_dir || '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="flex items-center justify-between px-4 py-3" style="border-top:1px solid var(--mm-border);">
        <span class="text-xs text-muted-foreground">{{ tab === 'active' ? `共 ${activeTasks.length} 条进行中任务` : `共 ${history.length} 条历史记录（SQLite 持久化）` }}</span>
        <span class="text-xs text-muted-foreground flex items-center gap-1">
          <Info class="w-3.5 h-3.5" />
          进行中列表每 3 秒自动刷新
        </span>
      </div>
    </section>

    <!-- ===== 归档对话框 ===== -->
    <div v-if="archiveTask" class="modal-overlay" @click.self="closeArchive">
      <div class="modal-panel" style="max-width: 42rem;">
        <div class="flex items-center justify-between px-5 py-4" style="border-bottom:1px solid var(--mm-border);">
          <div class="flex items-center gap-2">
            <span class="inline-flex items-center justify-center w-8 h-8 rounded-md" style="background:var(--mm-brand-50);color:var(--mm-primary);">
              <Archive class="w-4 h-4" />
            </span>
            <div>
              <h2 class="font-semibold text-foreground" style="font-size:15px;">归档专辑</h2>
              <p class="mono text-muted-foreground">{{ archiveTask.task_id }}</p>
            </div>
          </div>
          <button type="button" class="mm-btn btn-ghost" aria-label="关闭" @click="closeArchive">
            <X class="w-4 h-4" />
          </button>
        </div>

        <div class="px-5 py-4 flex flex-col gap-4">
          <p class="text-sm text-muted-foreground">{{ taskDisplayName(archiveTask) }}</p>

          <template v-if="!archiveResult">
            <div>
              <label class="text-xs font-medium text-muted-foreground block mb-1.5">目标媒体库</label>
              <select v-model="archiveForm.library" class="w-full h-9 px-3 rounded-md border border-input bg-background text-sm focus:outline-none focus:ring-2 focus:ring-ring">
                <option v-for="lib in libraries" :key="lib.name" :value="lib.name">{{ lib.name }}{{ lib.default ? '（默认库）' : '' }}</option>
              </select>
            </div>
            <div>
              <label class="text-xs font-medium text-muted-foreground block mb-1.5">合集归档（Various Artists）</label>
              <select v-model="archiveForm.compilation" class="w-full h-9 px-3 rounded-md border border-input bg-background text-sm focus:outline-none focus:ring-2 focus:ring-ring">
                <option value="auto">自动判定（按 VA 名单）</option>
                <option value="force">强制走合集归档（入库到 群星/）</option>
                <option value="plain">强制普通归档</option>
              </select>
            </div>
            <label class="flex items-center gap-2 text-sm text-foreground cursor-pointer">
              <input v-model="archiveForm.overwrite" type="checkbox" class="w-4 h-4 rounded border-input" style="accent-color: var(--mm-primary);" />
              目标已存在时覆盖重建（默认跳过，幂等）
            </label>
          </template>

          <!-- 归档结果 -->
          <template v-else>
            <div class="flex items-center gap-2">
              <span class="text-sm text-muted-foreground">归档结果：</span>
              <span class="mm-badge" :class="archiveResult.status === 'success' ? 'badge-success' : archiveResult.status === 'partial' ? 'badge-running' : 'badge-failed'">
                {{ archiveResult.status === 'success' ? '成功' : archiveResult.status === 'partial' ? '部分成功' : '失败' }}
              </span>
            </div>
            <div class="p-3 rounded-md" style="background:var(--mm-gray-50);border:1px solid var(--mm-border);">
              <div class="text-xs text-muted-foreground mb-1">库内目录</div>
              <div class="mono text-foreground" style="word-break:break-all;">{{ archiveResult.library_dir || '—' }}</div>
            </div>
            <div v-if="archiveResult.tracks?.length" class="rounded-md overflow-hidden" style="border:1px solid var(--mm-border);">
              <table class="mm-table">
                <thead>
                  <tr><th>曲目</th><th>标题</th><th>动作</th><th>歌词</th></tr>
                </thead>
                <tbody>
                  <tr v-for="(t, i) in archiveResult.tracks" :key="i">
                    <td class="mono text-muted-foreground">{{ t.disc > 1 ? `${t.disc}-` : '' }}{{ String(t.track).padStart(2, '0') }}</td>
                    <td>{{ t.title }}</td>
                    <td><span class="mm-badge" :class="archiveActionBadge(t.action)">{{ archiveActionText(t.action) }}</span></td>
                    <td class="text-muted-foreground text-xs">{{ t.lyric === 'ok' ? '已入库' : t.lyric === 'missing' ? '缺失' : '—' }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div v-if="archiveResult.errors?.length" class="rounded-md p-3 text-xs" style="background:#fef2f2;border:1px solid #fecaca;color:#b91c1c;">
              <div v-for="(err, i) in archiveResult.errors" :key="i" class="mono">{{ err }}</div>
            </div>
          </template>
        </div>

        <div class="flex items-center justify-end gap-2 px-5 py-4" style="border-top:1px solid var(--mm-border);">
          <button type="button" class="mm-btn btn-ghost" :disabled="archiving" @click="closeArchive">关闭</button>
          <button v-if="!archiveResult" type="button" class="mm-btn btn-primary" :disabled="archiving" @click="submitArchive">
            <Loader2 v-if="archiving" class="w-4 h-4 animate-spin" />
            {{ archiving ? '归档中...' : '开始归档' }}
          </button>
        </div>
      </div>
    </div>
  </AppShell>
</template>

<script setup>
import { computed, onActivated, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import {
  RefreshCw, Loader, Clock, CheckCircle2, XCircle, Activity, History, Database,
  Loader2, Check, X, MinusCircle, Ban, Archive, Info,
} from 'lucide-vue-next'
import AppShell from '../components/AppShell.vue'
import client, { errorMessage } from '../api/client'
import { useToastStore } from '../stores/toast'
import { taskDisplayName, TASK_STATUS_TEXT } from '../utils/format'

const toast = useToastStore()

const tasks = ref([])
const history = ref([])
const historyLoading = ref(false)
const refreshing = ref(false)
const tab = ref('active')
const cancelingId = ref('')
let pollTimer = null

const activeTasks = computed(() => tasks.value.filter((t) => ['pending', 'running'].includes(t.status)))
const runningTasks = computed(() => activeTasks.value.filter((t) => t.status === 'running'))
const pendingTasks = computed(() => activeTasks.value.filter((t) => t.status === 'pending'))

function isToday(ts) {
  if (!ts) return false
  const d = new Date(ts * 1000)
  const now = new Date()
  return d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth() && d.getDate() === now.getDate()
}

// 今日成功/失败基于历史记录（SQLite 持久化，带时间戳）本地统计
const todaySuccess = computed(() => history.value.filter((r) => r.status === 'success' && isToday(r.updated_at)).length)
const todayFailed = computed(() => history.value.filter((r) => r.status === 'failed' && isToday(r.updated_at)).length)

function progressPercent(task) {
  if (!task.total) return task.status === 'success' ? 100 : 0
  return Math.min(100, Math.round(((task.completed + task.failed) / task.total) * 100))
}

function durationText(row) {
  if (!row.created_at || !row.updated_at) return '—'
  const sec = Math.max(0, Math.round(row.updated_at - row.created_at))
  if (sec < 60) return `${sec} 秒`
  const m = Math.floor(sec / 60)
  const s = sec % 60
  return `${m} 分 ${String(s).padStart(2, '0')} 秒`
}

function timeText(ts) {
  if (!ts) return '—'
  const d = new Date(ts * 1000)
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

async function loadTasks(silent = false) {
  try {
    const { data } = await client.get('/downloads', { params: { limit: 50 } })
    tasks.value = data
  } catch (e) {
    if (!silent) toast.show(errorMessage(e, '获取任务列表失败'), 'error')
  }
}

async function loadHistory() {
  historyLoading.value = true
  try {
    const { data } = await client.get('/history', { params: { limit: 50 } })
    history.value = data
  } catch (e) {
    toast.show(errorMessage(e, '获取历史记录失败'), 'error')
  } finally {
    historyLoading.value = false
  }
}

async function refresh() {
  refreshing.value = true
  await Promise.all([loadTasks(), loadHistory()])
  refreshing.value = false
}

function switchTab(next) {
  tab.value = next
  if (next === 'history' && !history.value.length) loadHistory()
}

async function cancelTask(task) {
  cancelingId.value = task.task_id
  try {
    await client.post(`/downloads/${task.task_id}/cancel`)
    toast.show('任务已取消', 'success')
    await loadTasks(true)
  } catch (e) {
    toast.show(errorMessage(e, '取消任务失败'), 'error')
  } finally {
    cancelingId.value = ''
  }
}

function startPolling() {
  stopPolling()
  pollTimer = setInterval(() => loadTasks(true), 3000)
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

// ===== 归档对话框 =====
const archiveTask = ref(null)
const archiving = ref(false)
const archiveResult = ref(null)
const libraries = ref([{ name: 'default', default: true }])
const archiveForm = reactive({ library: 'default', compilation: 'auto', overwrite: false })

async function openArchive(task) {
  archiveTask.value = task
  archiveResult.value = null
  archiveForm.library = 'default'
  archiveForm.compilation = 'auto'
  archiveForm.overwrite = false
  try {
    const { data } = await client.get('/libraries')
    if (data.length) {
      libraries.value = data
      archiveForm.library = (data.find((l) => l.default) || data[0]).name
    }
  } catch {
    // 库列表拉取失败时保留 default 兜底
  }
}

function closeArchive() {
  if (archiving.value) return
  archiveTask.value = null
  archiveResult.value = null
}

async function submitArchive() {
  archiving.value = true
  try {
    const body = {
      task_id: archiveTask.value.task_id,
      library: archiveForm.library,
      overwrite: archiveForm.overwrite,
    }
    if (archiveForm.compilation === 'force') body.compilation = true
    else if (archiveForm.compilation === 'plain') body.compilation = false
    const { data } = await client.post('/albums/archive', body)
    archiveResult.value = data
    if (data.status === 'success') toast.show('归档完成', 'success')
    else if (data.status === 'partial') toast.show('部分曲目归档失败，请查看明细', 'warning')
    else toast.show('归档失败，请查看明细', 'error')
  } catch (e) {
    toast.show(errorMessage(e, '归档失败'), 'error')
  } finally {
    archiving.value = false
  }
}

function archiveActionText(action) {
  return { linked: '已链接', copied: '已复制', skipped: '已跳过', failed: '失败', tag_unsupported: '格式不支持写 tag' }[action] || action
}

function archiveActionBadge(action) {
  if (action === 'linked' || action === 'copied') return 'badge-success'
  if (action === 'skipped' || action === 'tag_unsupported') return 'badge-canceled'
  return 'badge-failed'
}

onMounted(() => {
  refresh()
  startPolling()
})

onActivated(() => {
  // keep-alive 场景恢复轮询
  startPolling()
})

onBeforeUnmount(stopPolling)
</script>
