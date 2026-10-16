// One media element owns the queue. Request generations prevent late URLs from
// restarting a stopped player or replacing a newer song.
export function createPreviewPlayer({ audio, resolveUrl, onChange = () => {} }) {
  const state = { queue: [], index: -1, status: 'idle', currentTime: 0, duration: 0, error: '' }
  let generation = 0, request, disposed = false, releasing = false, wantsPlayback = false
  const publish = () => { if (!disposed) onChange({ ...state }) }
  function release() {
    releasing = true
    audio.pause()
    audio.removeAttribute('src')
    audio.load()
    releasing = false
  }
  function invalidate() { generation++; request?.abort(); request = null }
  function updateTime() {
    if (releasing || !audio.src) return
    state.currentTime = Number.isFinite(audio.currentTime) ? audio.currentTime : 0
    state.duration = Number.isFinite(audio.duration) ? audio.duration : 0
    publish()
  }
  function fail(message) {
    wantsPlayback = false
    state.status = 'error'; state.error = message
    audio.pause()
    publish()
  }
  async function start(version) {
    try {
      await audio.play()
    } catch (error) {
      if (disposed || version !== generation || !wantsPlayback) return
      wantsPlayback = false
      if (error.name === 'NotAllowedError') {
        state.status = 'paused'; state.error = '浏览器需要确认，请点击播放继续试听'
        publish()
      } else if (error.name !== 'AbortError') {
        fail('音频无法播放，地址可能已过期或格式不受支持。请重试或切换歌曲。')
      }
    }
  }
  async function playAt(index) {
    if (disposed || index < 0 || index >= state.queue.length) return
    invalidate()
    const version = generation
    release()
    request = new AbortController()
    wantsPlayback = true
    Object.assign(state, { index, status: 'loading', currentTime: 0, duration: 0, error: '' })
    publish()
    try {
      const url = await resolveUrl(state.queue[index], { signal: request.signal })
      if (disposed || version !== generation) return
      request = null
      audio.src = url
      if (wantsPlayback) await start(version)
      else { state.status = 'paused'; publish() }
    } catch (error) {
      if (!disposed && version === generation) fail(error.message || '获取试听地址失败，请重试')
    }
  }
  async function playTracks(tracks) {
    if (disposed || !tracks.length) return
    state.queue = [...tracks]
    await playAt(0)
  }
  async function toggle() {
    if (disposed || state.index < 0) return
    if (wantsPlayback) {
      wantsPlayback = false
      audio.pause()
      state.status = 'paused'
      publish()
    } else if (!audio.src || ['error', 'ended', 'stopped'].includes(state.status)) {
      await playAt(state.index)
    } else {
      wantsPlayback = true; state.error = ''; state.status = 'loading'; publish()
      await start(generation)
    }
  }
  function stop() {
    invalidate(); wantsPlayback = false; release()
    Object.assign(state, { status: state.queue.length ? 'stopped' : 'idle', currentTime: 0, duration: 0, error: '' })
    publish()
  }
  function seek(seconds) {
    if (!audio.src || !state.duration || !Number.isFinite(Number(seconds))) return
    audio.currentTime = Math.max(0, Math.min(state.duration, Number(seconds)))
    updateTime()
  }
  const handlers = {
    loadedmetadata: updateTime, durationchange: updateTime, timeupdate: updateTime,
    playing: () => { if (!releasing && wantsPlayback) { state.status = 'playing'; state.error = ''; publish() } },
    waiting: () => { if (!releasing && wantsPlayback) { state.status = 'loading'; publish() } },
    pause: () => {
      if (!releasing && state.status === 'playing') { wantsPlayback = false; state.status = 'paused'; publish() }
    },
    error: () => { if (!releasing && audio.src) fail('音频加载失败，地址可能已过期或格式不受支持。请重试或切换歌曲。') },
    ended: () => {
      if (disposed || !audio.src) return
      if (state.index + 1 < state.queue.length) void playAt(state.index + 1)
      else { wantsPlayback = false; state.status = 'ended'; updateTime(); publish() }
    },
  }
  for (const [name, handler] of Object.entries(handlers)) audio.addEventListener(name, handler)
  function dispose() {
    if (disposed) return
    stop()
    for (const [name, handler] of Object.entries(handlers)) audio.removeEventListener(name, handler)
    disposed = true
  }
  publish()
  return { playTracks, playAt, toggle, stop, seek, dispose }
}
