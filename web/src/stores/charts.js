import { computed, reactive, ref, watch } from 'vue'
import { defineStore } from 'pinia'
import client, { errorMessage } from '../api/client'
import { useAuthStore } from './auth'
import { useToastStore } from './toast'

export const CHART_PLATFORMS = [{ id: 'netease', label: '网易云音乐' }, { id: 'qq', label: 'QQ 音乐' }]
export const ACTIVE_PARSE_STATUSES = ['pending', 'running', 'canceling']

// 解析及轮询属于会话状态，离开榜单路由后仍继续，结束后保存结果。
export const useChartsStore = defineStore('charts', () => {
  const auth = useAuthStore(), toast = useToastStore()
  const source = ref('netease'), charts = ref([]), loading = ref(false), current = ref(null)
  const parsing = ref(false), tracks = ref([]), selected = ref([]), limit = ref(20)
  const brokenCovers = reactive(new Set()), parseResults = reactive(new Map())
  const parseTask = ref(null), progressError = ref(''), stopping = ref(false)
  const observedAt = ref(Date.now()), now = ref(Date.now())
  const elapsed = computed(() => Math.floor((parseTask.value?.elapsed_s || 0) + (parsing.value ? (now.value - observedAt.value) / 1000 : 0)))
  const stalledSeconds = computed(() => Math.max(0, Math.floor(elapsed.value - ((parseTask.value?.updated_at || 0) - (parseTask.value?.created_at || 0)))))
  const progressPercent = computed(() => parseTask.value?.total ? Math.min(100, Math.floor(parseTask.value.processed / parseTask.value.total * 100)) : parseTask.value?.status === 'success' ? 100 : 0)
  let directoryController, tracksController, pollTimer, elapsedTimer

  function chartKey(chart) { return `${chart.source}:${chart.id}` }
  function chartSummary(chart) {
    if (current.value && chartKey(current.value) === chartKey(chart) && parsing.value) {
      if (parseTask.value?.status === 'canceling') return '正在停止解析…'
      if (parseTask.value?.status === 'success') return '解析完成，正在加载曲目结果…'
      return parseTask.value?.total != null ? `已处理 ${parseTask.value.processed}/${parseTask.value.total} 首 · 可下载 ${parseTask.value.available} 首 · ${elapsed.value} 秒` : `正在获取曲目目录 · ${elapsed.value} 秒`
    }
    const result = parseResults.get(chartKey(chart))
    if (result?.status === 'success') return `已解析 ${result.count} 首可下载曲目`
    if (result?.status === 'failed') return '解析失败，点击重试'
    if (result?.status === 'canceled') return '解析已停止，点击重试'
    return chart.track_count == null ? '点击解析曲目' : `${chart.track_count} 首`
  }
  function updateTask(task) {
    if (parseTask.value?.task_id === task.task_id && task.updated_at < parseTask.value.updated_at) return
    parseTask.value = task
    observedAt.value = now.value = Date.now()
  }
  function releaseParse(cancel = true) {
    clearTimeout(pollTimer); clearInterval(elapsedTimer); tracksController?.abort()
    if (cancel && auth.isAuthenticated && parseTask.value && ACTIVE_PARSE_STATUSES.includes(parseTask.value.status)) {
      client.delete(`/chart-parses/${parseTask.value.task_id}`, { timeout: 15000 }).catch(() => {})
    }
  }
  async function loadCharts() {
    directoryController?.abort()
    const controller = directoryController = new AbortController()
    loading.value = true
    try {
      const response = await client.get('/charts', { params: { source: source.value }, signal: controller.signal })
      if (!controller.signal.aborted) charts.value = response.data
    } catch (error) {
      if (!controller.signal.aborted) toast.show(errorMessage(error, '加载榜单目录失败'), 'error')
    } finally {
      if (!controller.signal.aborted) loading.value = false
    }
  }
  function ensureLoaded() { if (!charts.value.length && !loading.value) loadCharts() }
  function changeSource(value) {
    if (source.value === value) return
    releaseParse()
    source.value = value; charts.value = []; current.value = null; tracks.value = []; selected.value = []
    parsing.value = stopping.value = false; parseTask.value = null; progressError.value = ''
    loadCharts()
  }
  async function selectChart(chart, force = false) {
    if (!chart) return
    if (!force && current.value && chartKey(current.value) === chartKey(chart) && (parsing.value || parseTask.value?.status === 'success')) return
    releaseParse()
    const controller = tracksController = new AbortController()
    current.value = chart; tracks.value = []; selected.value = []; parsing.value = true
    parseTask.value = null; progressError.value = ''; stopping.value = false
    observedAt.value = now.value = Date.now()
    elapsedTimer = setInterval(() => { now.value = Date.now() }, 1000)
    try {
      // 切换到另一榜单时，等待提交返回 id 后取消已放弃的任务。
      const response = await client.post(`/charts/${chart.source}/${encodeURIComponent(chart.id)}/parse`, null, { params: limit.value ? { limit: limit.value } : {}, timeout: 15000 })
      if (controller.signal.aborted) {
        if (auth.isAuthenticated) client.delete(`/chart-parses/${response.data.task_id}`, { timeout: 15000 }).catch(() => {})
        return
      }
      updateTask(response.data)
      await pollParse(chart, response.data.task_id, controller)
    } catch (error) {
      if (!controller.signal.aborted) {
        parsing.value = false; clearInterval(elapsedTimer)
        parseResults.set(chartKey(chart), { status: 'failed' })
        parseTask.value = { status: 'failed', message: '创建解析任务失败', error: errorMessage(error), elapsed_s: elapsed.value, total: null, processed: 0, available: 0, skipped: 0 }
        toast.show(errorMessage(error, '解析榜单失败'), 'error')
      }
    }
  }
  async function pollParse(chart, taskId, controller) {
    if (controller.signal.aborted) return
    try {
      const response = await client.get(`/chart-parses/${taskId}`, { signal: controller.signal, timeout: 15000 })
      if (controller.signal.aborted) return
      updateTask(response.data); progressError.value = ''
      if (parseTask.value.status === 'success') {
        const result = await client.get(`/chart-parses/${taskId}/tracks`, { signal: controller.signal })
        if (controller.signal.aborted) return
        tracks.value = result.data
        parseResults.set(chartKey(chart), { status: 'success', count: tracks.value.length })
        parsing.value = false; clearInterval(elapsedTimer)
        toast.show(`解析完成，可下载 ${tracks.value.length} 首，跳过/失败 ${parseTask.value.skipped} 首`, 'success')
      } else if (['failed', 'canceled'].includes(parseTask.value.status)) {
        parseResults.set(chartKey(chart), { status: parseTask.value.status })
        parsing.value = false; clearInterval(elapsedTimer)
        if (parseTask.value.status === 'failed') toast.show(parseTask.value.error || '解析榜单失败', 'error')
      } else {
        pollTimer = setTimeout(() => pollParse(chart, taskId, controller), 1500)
      }
    } catch (error) {
      if (controller.signal.aborted) return
      if (error.response?.status >= 400 && error.response.status < 500 && error.response.status !== 429) {
        parsing.value = false; clearInterval(elapsedTimer)
        parseResults.set(chartKey(chart), { status: 'failed' })
        updateTask({ ...parseTask.value, status: 'failed', message: '无法继续获取解析进度', error: errorMessage(error) })
        if (error.response.status !== 401) toast.show(errorMessage(error), 'error')
      } else {
        progressError.value = errorMessage(error, '获取解析进度失败')
        pollTimer = setTimeout(() => pollParse(chart, taskId, controller), 3000)
      }
    }
  }
  async function stopParse() {
    if (!parseTask.value?.task_id || !parsing.value || stopping.value) return
    const controller = tracksController
    stopping.value = true
    try {
      const response = await client.delete(`/chart-parses/${parseTask.value.task_id}`, { signal: controller.signal, timeout: 15000 })
      if (!controller.signal.aborted) updateTask(response.data)
    } catch (error) { if (!controller.signal.aborted) toast.show(errorMessage(error, '停止解析失败'), 'error') }
    finally { if (!controller.signal.aborted) stopping.value = false }
  }
  function reset() {
    directoryController?.abort(); releaseParse(false)
    source.value = 'netease'; charts.value = []; current.value = null; tracks.value = []; selected.value = []; limit.value = 20
    loading.value = parsing.value = stopping.value = false; parseTask.value = null; progressError.value = ''
    brokenCovers.clear(); parseResults.clear()
  }
  watch(() => `${auth.apiKey}:${auth.isAuthenticated}`, reset, { flush: 'sync' })
  return { source, charts, loading, current, parsing, tracks, selected, limit, brokenCovers, parseTask, progressError, stopping, elapsed, stalledSeconds, progressPercent, chartSummary, loadCharts, ensureLoaded, changeSource, selectChart, stopParse, reset }
})
