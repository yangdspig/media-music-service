import test from 'node:test'
import assert from 'node:assert/strict'
import { createPreviewPlayer } from '../src/utils/preview-player.js'

// The browser supplies decoding; these tests exercise our queue and async lifecycle.
class AudioElement extends EventTarget {
  src = ''; currentTime = 0; duration = 120; paused = true
  async play() { this.paused = false; this.dispatchEvent(new Event('playing')) }
  pause() { this.paused = true; this.dispatchEvent(new Event('pause')) }
  load() {}
  removeAttribute(name) { if (name === 'src') this.src = '' }
}
const tracks = [{ id: 'one', title: '第一首' }, { id: 'two', title: '第二首' }, { id: 'three', title: '第三首' }]
const tick = () => new Promise(resolve => setImmediate(resolve))
function setup(resolveUrl = async track => `/audio/${track.id}`) {
  const audio = new AudioElement()
  let state
  const player = createPreviewPlayer({ audio, resolveUrl, onChange: value => { state = value } })
  return { audio, player, get state() { return state } }
}

test('selected queue plays in order and stops at its end', async () => {
  const ctx = setup()
  await ctx.player.playTracks([tracks[0], tracks[2]])
  assert.equal(ctx.state.queue.length, 2)
  assert.equal(ctx.audio.src, '/audio/one')
  ctx.audio.dispatchEvent(new Event('ended')); await tick()
  assert.equal(ctx.audio.src, '/audio/three')
  assert.equal(ctx.state.index, 1)
  ctx.audio.dispatchEvent(new Event('ended')); await tick()
  assert.equal(ctx.state.status, 'ended')
  assert.equal(ctx.state.index, 1)
  ctx.player.dispose()
})

test('pause resumes at position, seek clamps, and stop releases media and resets time', async () => {
  const ctx = setup()
  await ctx.player.playTracks(tracks)
  ctx.audio.dispatchEvent(new Event('loadedmetadata'))
  ctx.player.seek(45)
  await ctx.player.toggle()
  assert.equal(ctx.state.status, 'paused')
  assert.equal(ctx.audio.currentTime, 45)
  await ctx.player.toggle()
  assert.equal(ctx.state.status, 'playing')
  assert.equal(ctx.audio.currentTime, 45)
  ctx.player.seek(500)
  assert.equal(ctx.audio.currentTime, 120)
  ctx.player.stop()
  assert.equal(ctx.state.status, 'stopped')
  assert.equal(ctx.state.currentTime, 0)
  assert.equal(ctx.audio.src, '')
  await ctx.player.toggle()
  assert.equal(ctx.audio.src, '/audio/one')
  ctx.player.dispose()
})

test('late resolution cannot overwrite the latest song', async () => {
  let finishFirst
  const ctx = setup(track => track.id === 'one' ? new Promise(resolve => { finishFirst = resolve }) : Promise.resolve(`/audio/${track.id}`))
  const first = ctx.player.playTracks(tracks)
  await ctx.player.playAt(1)
  finishFirst('/audio/one'); await first
  assert.equal(ctx.audio.src, '/audio/two')
  assert.equal(ctx.state.index, 1)
  ctx.player.dispose()
})

test('stop during URL request prevents delayed playback', async () => {
  let finish
  const ctx = setup(() => new Promise(resolve => { finish = resolve }))
  const pending = ctx.player.playTracks(tracks)
  ctx.player.stop()
  finish('/audio/one'); await pending
  assert.equal(ctx.audio.src, '')
  assert.equal(ctx.state.status, 'stopped')
  ctx.player.dispose()
})

test('loading can be paused before the URL arrives', async () => {
  let finish
  const ctx = setup(() => new Promise(resolve => { finish = resolve }))
  const pending = ctx.player.playTracks(tracks)
  await ctx.player.toggle()
  finish('/audio/one'); await pending
  assert.equal(ctx.audio.paused, true)
  assert.equal(ctx.state.status, 'paused')
  await ctx.player.toggle()
  assert.equal(ctx.state.status, 'playing')
  ctx.player.dispose()
})

test('audio failure is recoverable and retries with a new preview URL', async () => {
  let request = 0
  const ctx = setup(async () => `/audio/version-${++request}`)
  await ctx.player.playTracks(tracks)
  ctx.audio.dispatchEvent(new Event('error'))
  assert.equal(ctx.state.status, 'error')
  assert.ok(ctx.state.error)
  await ctx.player.toggle()
  assert.equal(ctx.audio.src, '/audio/version-2')
  assert.equal(ctx.state.status, 'playing')
  ctx.player.dispose()
})

test('autoplay rejection asks for a user gesture and can resume', async () => {
  const ctx = setup()
  ctx.audio.play = async () => { throw Object.assign(new Error(), { name: 'NotAllowedError' }) }
  await ctx.player.playTracks([tracks[0]])
  assert.equal(ctx.state.status, 'paused')
  assert.match(ctx.state.error, /播放/)
  ctx.audio.play = AudioElement.prototype.play
  await ctx.player.toggle()
  assert.equal(ctx.state.status, 'playing')
  assert.equal(ctx.state.error, '')
  ctx.player.dispose()
})

test('dispose aborts requests and detaches automatic advancement', async () => {
  let finish, signal
  const ctx = setup((_, options) => { signal = options.signal; return new Promise(resolve => { finish = resolve }) })
  const pending = ctx.player.playTracks(tracks)
  ctx.player.dispose()
  assert.equal(signal.aborted, true)
  finish('/audio/one'); await pending
  ctx.audio.dispatchEvent(new Event('ended')); await tick()
  assert.equal(ctx.audio.src, '')
})
