import { computed, reactive, ref, watch } from 'vue'
import { defineStore } from 'pinia'
import client, { errorMessage } from '../api/client'
import { useAuthStore } from './auth'
import { useToastStore } from './toast'

// 只在当前登录会话中保留；路由切换不会重新创建搜索状态。
export const useSearchStore = defineStore('search', () => {
  const auth = useAuthStore(), toast = useToastStore()
  const keyword = ref(''), prefilledKeyword = ref(null), limit = ref(50), sortOrder = ref('original')
  const searchSources = ref([]), selectedSources = reactive(new Set())
  const tracks = ref([]), failedSources = ref([]), selected = reactive(new Set())
  const searched = ref(false), searching = ref(false), sourcesLoading = ref(false), sourcesReady = ref(false)
  const defaultsSignature = ref(null)
  const defaultSources = ref([])
  let defaultsRevision = 0
  let sourcesController, searchController

  const sortedTracks = computed(() => {
    if (sortOrder.value === 'original') return tracks.value
    const size = track => track.size_bytes == null || !Number.isFinite(Number(track.size_bytes)) || Number(track.size_bytes) <= 0 ? null : Number(track.size_bytes)
    return [...tracks.value].sort((a, b) => {
      const left = size(a), right = size(b)
      if (left == null) return right == null ? 0 : 1
      if (right == null) return -1
      return sortOrder.value === 'size-desc' ? right - left : left - right
    })
  })

  function applyDefaults(names) {
    const defaults = [...new Set(names || [])], signature = JSON.stringify([...defaults].sort())
    defaultSources.value = defaults
    defaultsRevision++
    if (signature !== defaultsSignature.value) {
      selectedSources.clear()
      defaults.forEach(name => selectedSources.add(name))
      defaultsSignature.value = signature
    }
    // 默认来源排在候选来源前面，排序与配置的顺序一致。
    const order = new Map(defaults.map((name, index) => [name, index]))
    searchSources.value = [...searchSources.value].sort((a, b) => (order.get(a.name) ?? Infinity) - (order.get(b.name) ?? Infinity))
  }

  async function loadSources() {
    if (sourcesLoading.value) return
    const controller = sourcesController = new AbortController()
    const revision = defaultsRevision
    sourcesLoading.value = true
    try {
      const [sources, config] = await Promise.all([
        client.get('/sources', { signal: controller.signal }),
        client.get('/config', { signal: controller.signal }),
      ])
      if (controller.signal.aborted) return
      searchSources.value = sources.data.filter(source => source.supports_search !== false)
      applyDefaults(revision === defaultsRevision ? config.data.default_sources : defaultSources.value)
      sourcesReady.value = true
    } catch (error) {
      if (!controller.signal.aborted) toast.show(errorMessage(error, '获取默认搜索来源失败'), 'error')
    } finally {
      if (!controller.signal.aborted) sourcesLoading.value = false
    }
  }

  async function doSearch() {
    if (searching.value) return
    if (!keyword.value.trim()) { toast.show('请输入搜索关键词', 'warning'); return }
    if (!sourcesReady.value) { await loadSources(); if (!sourcesReady.value) return }
    if (!selectedSources.size) { toast.show('请至少选择一个搜索来源', 'warning'); return }
    const requestedSources = [...selectedSources]
    const available = new Set(searchSources.value.filter(source => source.available).map(source => source.name))
    const sources = requestedSources.filter(name => available.has(name))
    if (!sources.length) { toast.show('所选来源当前不可用，请检查登录或选择其他来源', 'warning'); return }
    const controller = searchController = new AbortController()
    searching.value = true
    searched.value = true
    // 新搜索成功后才替换原结果，网络错误时仍可操作上次结果。
    try {
      const { data } = await client.get('/search', {
        params: { keyword: keyword.value.trim(), limit: limit.value, sources: sources.join(',') },
        signal: controller.signal,
      })
      if (controller.signal.aborted) return
      tracks.value = data.tracks || []
      failedSources.value = [
        ...(data.failed_sources || []),
        ...requestedSources.filter(name => !available.has(name)).map(source => ({ source, error: '来源不可用，请检查登录或配置' })),
      ]
      selected.clear()
    } catch (error) {
      if (!controller.signal.aborted) toast.show(errorMessage(error, '搜索失败，请稍后重试'), 'error')
    } finally {
      if (!controller.signal.aborted) searching.value = false
    }
  }

  function reset() {
    sourcesController?.abort(); searchController?.abort()
    keyword.value = ''; prefilledKeyword.value = null; limit.value = 50; sortOrder.value = 'original'
    searchSources.value = []; selectedSources.clear(); selected.clear()
    tracks.value = []; failedSources.value = []; defaultsSignature.value = null; defaultSources.value = []; defaultsRevision++
    searched.value = searching.value = sourcesLoading.value = sourcesReady.value = false
  }
  watch(() => `${auth.apiKey}:${auth.isAuthenticated}`, reset, { flush: 'sync' })

  return { keyword, prefilledKeyword, limit, sortOrder, sortedTracks, searchSources, selectedSources, tracks, failedSources, selected, searched, searching, sourcesLoading, sourcesReady, loadSources, doSearch, applyDefaults, reset }
})
