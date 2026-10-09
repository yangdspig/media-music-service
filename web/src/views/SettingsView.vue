<template>
  <AppShell>
    <header class="flex flex-wrap items-center justify-between gap-3 mb-5"><div><h1 class="text-xl font-semibold">系统配置</h1><p class="text-sm text-muted-foreground mt-1">配置保存到 config.yaml；凭证只显示脱敏值。</p></div><button class="mm-btn btn-outline" :disabled="loading || saving || dirty" @click="load"><RefreshCw class="w-4 h-4" :class="{ 'animate-spin': loading }" />重新读取</button></header>
    <p v-if="loading" class="mm-card p-8 text-sm text-muted-foreground" role="status">正在读取配置…</p>
    <p v-if="error" class="mm-notice mb-4 text-[var(--state-error)]" role="alert">{{ error }}<button v-if="!draft" class="mm-btn btn-ghost ml-2" @click="load">重试</button></p>
    <template v-if="draft">
      <p v-if="editor.pending_restart.length" class="mm-notice mb-4">已保存，等待重启：{{ editor.pending_restart.map(labelFor).join('、') }}。表单显示已保存的值。</p>
      <p v-if="notice" class="mm-notice mb-4" role="status">{{ notice }}</p>
      <form id="config-form" class="space-y-4 pb-8" @submit.prevent="save">
        <fieldset :disabled="saving" class="space-y-4 min-w-0">
          <details v-for="group in groups" :key="group.id" class="mm-card" :open="group.id === 'server'">
            <summary class="cursor-pointer px-4 py-4 font-semibold text-sm">{{ group.title }}<span class="text-xs font-normal text-muted-foreground ml-2">{{ group.description }}</span></summary>
            <div class="grid gap-4 sm:grid-cols-2 border-t border-border p-4">
              <ConfigField v-for="field in group.fields" :key="field.key" :field="field" :model-value="editor.environment_overrides.includes(field.key) ? editor.runtime[field.key] : read(field.key)" :mode="editor.modes[field.key.split('.')[0]]" :disabled="editor.environment_overrides.includes(field.key)" @update:model-value="write(field.key, $event)" />
              <ConfigMapEditor v-if="group.id === 'paths'" :key="`roots-${formVersion}`" v-model="draft.extra_library_roots" class="sm:col-span-2" label="命名媒体库 · 重启生效" help='格式：{"singles": "/singles"}。库名用于下载和归档时选择目标库；路径需已挂载进容器。' string-values @invalid="invalidMaps.roots = $event" />
              <div v-if="group.id === 'download'" class="sm:col-span-2 space-y-2"><div class="flex flex-wrap items-center gap-3"><p class="text-sm">默认搜索来源 <span class="mm-badge badge-success">即时生效</span></p><button type="button" class="mm-btn btn-ghost" @click="draft.default_sources = [...DEFAULT_SEARCH_SOURCES]">使用常用六源</button></div><div class="flex flex-wrap gap-3"><label v-for="source in sources" :key="source.name" class="inline-flex gap-2 items-center text-xs"><input v-model="draft.default_sources" type="checkbox" class="mm-checkbox" :value="source.name" />{{ sourceDisplayName(source.name) }}</label></div><p class="text-xs text-muted-foreground">搜索页使用这里保存的默认来源；保存修改后同步勾选。禁用或不可用的来源会在实际搜索时被过滤。</p></div>
              <div v-if="group.id === 'auth'" class="sm:col-span-2 space-y-2"><button type="button" class="mm-btn btn-outline" :disabled="refreshingAuth" @click="refreshAuth"><LoaderCircle v-if="refreshingAuth" class="w-4 h-4 animate-spin" />立即刷新已生效的 QQ 凭证</button><p v-if="authResult" class="text-xs break-words" role="status">{{ authResult }}</p><p class="text-xs text-muted-foreground">检查周期从保存时重新计时；关闭开关后，下一次检查会跳过。</p></div>
            </div>
          </details>
          <details class="mm-card" :open="route.query.section === 'sources'"><summary class="cursor-pointer px-4 py-4 font-semibold text-sm">音乐来源与登录 <span class="mm-badge badge-success ml-2">即时生效</span></summary>
            <div class="border-t border-border p-4 space-y-4"><p class="text-xs text-muted-foreground">保持掩码表示保留原凭证；输入新内容替换，清空会删除该项。扫码登录成功后会自动保存该来源的凭证。</p>
              <details v-for="name in sourceNames" :key="name" class="border border-border rounded-md" :open="['QQMusicClient', 'NeteaseMusicClient'].includes(name)">
                <summary class="p-3 cursor-pointer text-sm font-medium">{{ sourceDisplayName(name) }} <span class="text-xs font-normal text-muted-foreground ml-2">{{ sources.find(s => s.name === name)?.available ? '可用' : '请检查配置或平台状态' }}</span></summary>
                <div class="p-3 border-t border-border space-y-4"><div class="flex flex-wrap items-center justify-between gap-3"><label class="flex gap-2 items-center text-sm"><input v-model="draft.sources[name].enabled" type="checkbox" class="mm-checkbox" />启用此来源</label><button v-if="['QQMusicClient', 'NeteaseMusicClient'].includes(name)" type="button" class="mm-btn btn-primary" @click="qrSource = name === 'QQMusicClient' ? 'qq' : 'netease'"><QrCode class="w-4 h-4" />扫码登录</button></div><p v-if="sources.find(s => s.name === name)?.note" class="text-xs text-muted-foreground break-words">{{ sources.find(s => s.name === name)?.note }}</p>
                  <div class="grid gap-4 sm:grid-cols-2"><ConfigField v-for="field in cookieFields" :key="field.key" :field="field" v-model="draft.sources[name][field.key]" /></div>
                  <details><summary class="text-xs text-muted-foreground cursor-pointer">高级来源参数</summary><ConfigMapEditor :key="`${name}-${formVersion}`" v-model="draft.sources[name].extra" class="mt-3" label="来源参数（JSON 对象）" help="按该来源支持的参数填写；空对象表示清除覆盖参数。" @invalid="invalidMaps[name] = $event" /></details>
                </div>
              </details>
            </div>
          </details>
          <details class="mm-card" :open="route.query.section === 'fnos'"><summary class="cursor-pointer px-4 py-4 font-semibold text-sm">飞牛音乐 <span class="mm-badge badge-success ml-2">即时生效</span></summary>
            <div class="border-t border-border p-4 space-y-4"><label class="flex gap-2 items-center text-sm"><input type="checkbox" class="mm-checkbox" :checked="!!draft.fnos_music" @change="toggleFnos($event.target.checked)" />启用飞牛音乐连接</label><p class="text-xs text-muted-foreground">使用飞牛音乐应用账号；库路径映射用于把容器文件关联到飞牛已扫描的曲目。</p>
              <div v-if="draft.fnos_music" class="grid gap-4 sm:grid-cols-2"><ConfigField v-for="field in fnosFields" :key="field.key" :field="field" v-model="draft.fnos_music[field.key]" /><ConfigMapEditor :key="`fnos-${formVersion}`" v-model="draft.fnos_music.path_map" class="sm:col-span-2" label="容器路径 → 飞牛宿主路径" help='例如 {"/library": "/vol1/1000/Media/Music", "/singles": "/vol1/1000/Media/Music/singles"}。删除映射后保存即可生效。' string-values @invalid="invalidMaps.fnos = $event" /></div>
              <div class="flex flex-wrap items-center gap-3"><button type="button" class="mm-btn btn-outline" :disabled="checking || !editor.runtime.fnos_music" @click="checkConnection"><LoaderCircle v-if="checking" class="w-4 h-4 animate-spin" />检查已生效连接</button><RouterLink to="/fnos" class="text-sm text-primary">进入飞牛歌单</RouterLink></div><p v-if="connection" class="text-sm" role="status">{{ connection }}</p>
            </div>
          </details>
          <details class="mm-card"><summary class="cursor-pointer px-4 py-4 font-semibold text-sm">MCP 适配器 <span class="mm-badge badge-pending ml-2">只读</span></summary><div class="p-4 border-t border-border grid gap-3 sm:grid-cols-2 text-sm"><p v-for="(value, key) in editor.config.mcp" :key="key" class="break-all"><span class="text-muted-foreground">{{ key }}：</span><span class="mono">{{ value }}</span></p><p class="sm:col-span-2 text-xs text-muted-foreground">适配器单独运行；如需修改，请编辑配置并重启对应 MCP 容器。环境变量可覆盖适配器配置。</p></div></details>
        </fieldset>
      </form>
      <div class="config-save-bar sticky z-30 bg-card/95 backdrop-blur rounded-md border border-border shadow-md p-3 flex flex-wrap items-center justify-between gap-3">
        <div class="text-xs text-muted-foreground min-w-0"><p>{{ changeCount ? `有 ${changeCount} 项未保存` : mapsInvalid ? '有未保存的输入' : '当前配置已保存' }}{{ mapsInvalid ? ' · JSON 参数格式有误' : '' }}</p><p v-if="restartChanges.length" class="mt-1 break-words">本次需重启：{{ restartChanges.map(labelFor).join('、') }}</p></div><div class="flex gap-2"><button class="mm-btn btn-outline" :disabled="!dirty || saving" @click="discard">撤销修改</button><button type="submit" form="config-form" class="mm-btn btn-primary" :disabled="!changeCount || mapsInvalid || saving"><Save class="w-4 h-4" />{{ saving ? '正在保存…' : '保存配置' }}</button></div>
      </div>
    </template>
    <QrLoginDialog v-if="qrSource" :source="qrSource" @close="qrSource = null" @saved="qrSaved" />
  </AppShell>
</template>
<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'
import { LoaderCircle, QrCode, RefreshCw, Save } from 'lucide-vue-next'
import AppShell from '../components/AppShell.vue'
import ConfigField from '../components/ConfigField.vue'
import ConfigMapEditor from '../components/ConfigMapEditor.vue'
import QrLoginDialog from '../components/QrLoginDialog.vue'
import client, { errorMessage } from '../api/client'
import { sourceDisplayName } from '../utils/format'
import { DEFAULT_SEARCH_SOURCES } from '../utils/sources'
import { useSearchStore } from '../stores/search'
import { useAuthStore } from '../stores/auth'
import { useToastStore } from '../stores/toast'
const route = useRoute(), router = useRouter(), auth = useAuthStore(), toast = useToastStore()
const searchStore = useSearchStore()
const editor = ref(null), draft = ref(null), baseline = ref(null), sources = ref([]), loading = ref(false), saving = ref(false), error = ref(''), notice = ref(''), qrSource = ref(null), invalidMaps = ref({}), formVersion = ref(0), checking = ref(false), connection = ref('')
const refreshingAuth = ref(false), authResult = ref('')
let fnosDraft = null, disposed = false
const copy = value => JSON.parse(JSON.stringify(value))
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b)
const cookieFields = [{ key: 'search_cookies', label: '搜索凭证', type: 'secret' }, { key: 'download_cookies', label: '下载凭证', type: 'secret' }, { key: 'parse_cookies', label: '解析凭证', type: 'secret' }, { key: 'quark_cookies', label: '夸克凭证', type: 'secret' }]
const groups = [
  { id: 'server', title: '服务与安全', description: '监听地址和访问密钥', fields: [
    { key: 'host', label: '监听地址', help: '容器通常使用 0.0.0.0。' }, { key: 'port', label: '监听端口', type: 'number', min: 1, max: 65535 }, { key: 'api_key', label: 'API Key', type: 'secret', wide: true, help: '修改后需重新登录；清空表示关闭访问鉴权。' }] },
  { id: 'download', title: '下载与归档', description: '并发、体积和来源', fields: [
    { key: 'num_threads', label: '每个来源的线程数', type: 'number', min: 1, max: 128 }, { key: 'download_timeout_s', label: '下载读取超时（秒）', type: 'number', min: 1, help: 'HTTP 读取等待时限，适用于后续下载。' }, { key: 'max_size_mb', label: '单文件体积上限（MB）', type: 'number', min: 0, step: 'any', help: '空值或 0 表示不限。' }, { key: 'archive_comment', label: '归档备注' }] },
  { id: 'paths', title: '下载与媒体库路径', description: '重启后使用新路径', fields: [
    { key: 'download_root', label: '下载目录' }, { key: 'db_path', label: '数据库路径' }, { key: 'library_root', label: '默认媒体库根目录', nullable: true, wide: true, help: '容器内绝对路径；空值表示未配置默认归档库。' }] },
  { id: 'cleanup', title: '下载目录清理', description: '只处理服务管理的下载产物', fields: [
    { key: 'cleanup.after_archive', label: '归档后清理', type: 'bool' }, { key: 'cleanup.periodic', label: '启用定期清理', type: 'bool' }, { key: 'cleanup.interval_s', label: '扫描间隔（秒）', type: 'number', min: 1 }, { key: 'cleanup.max_size_gb', label: '占用阈值（GB）', type: 'number', min: 0.001, step: 'any' }, { key: 'cleanup.keep_hours', label: '近期任务保护时长（小时）', type: 'number', min: 0, step: 'any' }] },
  { id: 'auth', title: 'QQ 登录保活', description: '自动续期与手动刷新', fields: [
    { key: 'auth_refresh.enabled', label: '自动保活', type: 'bool' }, { key: 'auth_refresh.interval_s', label: '检查间隔（秒）', type: 'number', min: 1 }] },
]
const fnosFields = [{ key: 'base_url', label: '飞牛音乐地址', placeholder: 'https://192.168.254.111:5667' }, { key: 'username', label: '应用账号' }, { key: 'password', label: '应用密码', type: 'secret' }, { key: 'scan_wait_s', label: '等待扫描上限（秒）', type: 'number', min: 0 }, { key: 'verify_tls', label: '验证 TLS 证书', type: 'bool', help: '自签证书部署可关闭校验。' }]
const sourceNames = computed(() => [...new Set([...sources.value.map(s => s.name), ...Object.keys(draft.value?.sources || {})])])
function normalize(config) {
  const data = copy(config)
  for (const name of new Set([...sources.value.map(s => s.name), ...Object.keys(data.sources)])) data.sources[name] = { enabled: true, search_cookies: null, download_cookies: null, parse_cookies: null, quark_cookies: null, extra: {}, ...data.sources[name] }
  return data
}
function read(path) { return path.split('.').reduce((value, key) => value?.[key], draft.value) }
function write(path, value) { const keys = path.split('.'), key = keys.pop(); const parent = keys.reduce((value, name) => value[name], draft.value); parent[key] = value }
function diff(next, old) {
  const result = {}
  for (const [key, value] of Object.entries(next)) {
    if (same(value, old?.[key])) continue
    if (['sources', 'cleanup', 'auth_refresh', 'fnos_music'].includes(key) && value && old?.[key]) {
      const sub = key === 'sources' ? diffSources(value, old[key]) : Object.fromEntries(Object.entries(value).filter(([k, v]) => !same(v, old[key][k])))
      if (Object.keys(sub).length) result[key] = sub
    } else result[key] = value
  }
  return result
}
function diffSources(next, old) { return Object.fromEntries(Object.entries(next).map(([name, config]) => [name, Object.fromEntries(Object.entries(config).filter(([key, value]) => !same(value, old[name]?.[key])))]).filter(([, value]) => Object.keys(value).length)) }
const updates = computed(() => draft.value && baseline.value ? diff(draft.value, baseline.value) : {})
const changeCount = computed(() => Object.entries(updates.value).reduce((n, [key, value]) => n + (key === 'sources' ? Object.values(value).reduce((sum, v) => sum + Object.keys(v).length, 0) : value && typeof value === 'object' && !Array.isArray(value) && !['extra_library_roots'].includes(key) ? Object.keys(value).length : 1), 0))
const mapsInvalid = computed(() => Object.values(invalidMaps.value).some(Boolean))
const dirty = computed(() => !!changeCount.value || mapsInvalid.value)
const restartChanges = computed(() => Object.keys(updates.value).filter(key => editor.value?.modes[key] === 'restart'))
function labelFor(key) { return groups.flatMap(g => g.fields).find(f => f.key === key)?.label || { extra_library_roots: '命名媒体库', mcp: 'MCP 适配器' }[key] || key }
async function load() {
  loading.value = true; error.value = ''
  try {
    const [configResponse, sourceResponse] = await Promise.all([client.get('/config/editor'), client.get('/sources')])
    if (disposed) return
    sources.value = sourceResponse.data; editor.value = configResponse.data; resetDraft()
  } catch (e) { if (!disposed) error.value = errorMessage(e) }
  finally { if (!disposed) loading.value = false }
}
function resetDraft() { baseline.value = normalize(editor.value.config); draft.value = copy(baseline.value); fnosDraft = copy(draft.value.fnos_music); invalidMaps.value = {}; formVersion.value++ }
function discard() { draft.value = copy(baseline.value); invalidMaps.value = {}; error.value = ''; formVersion.value++ }
function toggleFnos(enabled) { if (enabled) draft.value.fnos_music = fnosDraft || { base_url: '', username: '', password: '', verify_tls: false, scan_wait_s: 120, path_map: {} }; else { fnosDraft = copy(draft.value.fnos_music); draft.value.fnos_music = null; invalidMaps.value.fnos = false } }
async function save() {
  if (saving.value || !changeCount.value || mapsInvalid.value) return
  if (!draft.value.default_sources.length) { error.value = '请至少选择一个默认搜索来源'; return }
  if (draft.value.fnos_music && (!/^https?:\/\//.test(draft.value.fnos_music.base_url) || !draft.value.fnos_music.username.trim() || !draft.value.fnos_music.password)) { error.value = '请填写完整的飞牛地址、账号和密码'; return }
  saving.value = true; error.value = ''; notice.value = ''
  const keyChanged = Object.hasOwn(updates.value, 'api_key')
  try {
    const { data } = await client.put('/config', updates.value)
    if (disposed) return
    editor.value = data.editor; resetDraft()
    searchStore.applyDefaults(data.editor.runtime.default_sources)
    notice.value = data.editor.pending_restart.length ? '配置已保存。即时生效项目已应用，其余项目请重启对应服务。' : '配置已保存并生效。'
    toast.show('配置已保存')
    if (keyChanged) { auth.clear(); router.replace({ path: '/login', query: { redirect: '/settings' } }) }
  } catch (e) { if (!disposed) error.value = errorMessage(e, '保存失败，请检查配置与写入权限') }
  finally { if (!disposed) saving.value = false }
}
async function qrSaved(source) {
  try {
    const { data } = await client.get('/config/editor')
    if (disposed) return
    const name = source === 'qq' ? 'QQMusicClient' : 'NeteaseMusicClient'
    for (const key of ['search_cookies', 'download_cookies', 'parse_cookies']) {
      if (same(draft.value.sources[name][key], baseline.value.sources[name][key])) draft.value.sources[name][key] = data.config.sources[name][key]
      baseline.value.sources[name][key] = data.config.sources[name][key]
    }
    editor.value = data; notice.value = '扫码凭证已保存并生效。表单中的其他草稿仍需点击保存；手动编辑过的凭证草稿也会保留。'; toast.show('扫码登录成功')
    const response = await client.get('/sources'); if (!disposed) sources.value = response.data
  } catch (e) { if (!disposed) error.value = '登录已保存，但页面更新失败，请重新读取配置：' + errorMessage(e) }
}
async function checkConnection() { checking.value = true; connection.value = ''; try { const { data } = await client.get('/fnos/playlists'); if (!disposed) connection.value = `连接成功 · 当前 ${data.length} 个歌单` } catch (e) { if (!disposed) connection.value = errorMessage(e) } finally { if (!disposed) checking.value = false } }
async function refreshAuth() {
  refreshingAuth.value = true; authResult.value = ''
  try {
    const { data } = await client.post('/auth/qq/refresh')
    if (disposed) return
    authResult.value = ({ refreshed: 'QQ 凭证已刷新', fresh: '当前凭证仍然有效', skipped: '未配置 QQ 凭证，请先扫码登录', expired: '凭证已失效，请重新扫码登录', failed: '凭证刷新失败，请稍后重试或重新扫码' })[data.status] || '刷新未完成'
    const response = await client.get('/sources'); if (!disposed) sources.value = response.data
  } catch (e) { if (!disposed) authResult.value = errorMessage(e) }
  finally { if (!disposed) refreshingAuth.value = false }
}
function beforeUnload(event) { if (dirty.value) { event.preventDefault(); event.returnValue = '' } }
onBeforeRouteLeave(to => to.path === '/login' || !dirty.value || window.confirm('有未保存的配置，确定离开并放弃这些修改吗？'))
onMounted(() => { load(); window.addEventListener('beforeunload', beforeUnload) })
onBeforeUnmount(() => { disposed = true; window.removeEventListener('beforeunload', beforeUnload) })
</script>
<style scoped>
.config-save-bar { bottom: calc(4.5rem + env(safe-area-inset-bottom)); }
@media (min-width: 960px) { .config-save-bar { bottom: 1rem; } }
</style>
