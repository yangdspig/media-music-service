<template>
  <ModalDialog :title="`${source === 'qq' ? 'QQ 音乐' : '网易云音乐'}扫码登录`" width="30rem" @close="emit('close')">
    <div class="text-center space-y-4">
      <p class="text-sm text-muted-foreground">请使用{{ source === 'qq' ? 'QQ' : '网易云音乐' }} App 扫码，并在手机上确认。</p>
      <div class="mx-auto rounded-md border border-border bg-white p-3 relative" style="width:min(280px,100%);aspect-ratio:1">
        <img v-if="session?.image_url" :src="session.image_url" class="w-full h-full object-contain" alt="登录二维码" />
        <div v-if="loading || ['expired', 'failed', 'success'].includes(session?.status)" class="absolute inset-0 flex flex-col items-center justify-center gap-3 rounded-md bg-white/95 text-slate-700 p-4"><LoaderCircle v-if="loading" class="w-7 h-7 animate-spin" /><CheckCircle2 v-else-if="session.status === 'success'" class="w-9 h-9 text-emerald-600" /><RefreshCw v-else class="w-7 h-7" /><p class="text-sm">{{ loading ? '正在生成二维码…' : session.message }}</p></div>
      </div>
      <p role="status" aria-live="polite" class="text-sm break-words" :class="session?.status === 'success' ? 'text-[var(--state-success)]' : session?.status === 'scanned' ? 'text-[var(--state-warning)]' : ['failed', 'expired'].includes(session?.status) ? 'text-[var(--state-error)]' : 'text-muted-foreground'">{{ session?.message || '等待生成二维码' }}</p>
      <p v-if="session && !['success', 'expired', 'failed'].includes(session.status)" class="text-xs text-muted-foreground">{{ remaining }} 秒后过期</p>
      <p v-if="error" role="alert" class="text-sm text-[var(--state-error)] break-words">{{ error }}</p>
      <p class="text-xs text-muted-foreground leading-relaxed">确认成功后，凭证会自动保存并立即生效。其他未保存的配置保持在当前表单中。</p>
    </div>
    <template #footer><button class="mm-btn btn-outline" @click="emit('close')">关闭</button><button class="mm-btn btn-primary" :disabled="loading || session?.status === 'success'" @click="generate"><RefreshCw class="w-4 h-4" />刷新二维码</button></template>
  </ModalDialog>
</template>
<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { CheckCircle2, LoaderCircle, RefreshCw } from 'lucide-vue-next'
import client, { errorMessage } from '../api/client'
import ModalDialog from './ModalDialog.vue'
const props = defineProps({ source: { type: String, required: true } })
const emit = defineEmits(['close', 'saved'])
const session = ref(null), loading = ref(false), error = ref(''), now = ref(Date.now())
let timer, clock, generation = 0, disposed = false
const remaining = computed(() => Math.max(0, Math.ceil((session.value?.expires_at * 1000 - now.value) / 1000)))
function release(key) { if (key) client.delete(`/auth/qr/${props.source}`, { params: { key } }).catch(() => {}) }
async function generate() {
  const version = ++generation
  clearTimeout(timer); release(session.value?.key); session.value = null; error.value = ''; loading.value = true
  try {
    const { data } = await client.post(`/auth/qr/${props.source}`)
    if (disposed || version !== generation) { release(data.key); return }
    session.value = data; timer = setTimeout(() => poll(version), 2200)
  } catch (e) { if (!disposed && version === generation) error.value = errorMessage(e) }
  finally { if (!disposed && version === generation) loading.value = false }
}
async function poll(version) {
  if (disposed || version !== generation || !session.value) return
  try {
    const { data } = await client.get(`/auth/qr/${props.source}`, { params: { key: session.value.key } })
    if (disposed || version !== generation) return
    session.value = { ...session.value, ...data }; error.value = ''
    if (data.status === 'success') { emit('saved', props.source); timer = setTimeout(() => emit('close'), 900); return }
    if (['failed', 'expired'].includes(data.status)) return
  } catch (e) {
    if (disposed || version !== generation) return
    error.value = errorMessage(e)
    if ([400, 401, 404].includes(e.response?.status)) { session.value.status = 'failed'; return }
  }
  if (remaining.value <= 0) { session.value.status = 'expired'; session.value.message = '二维码已过期，请刷新'; release(session.value.key); return }
  timer = setTimeout(() => poll(version), error.value ? 4000 : 2200)
}
onMounted(() => { clock = setInterval(() => { now.value = Date.now() }, 1000); generate() })
onBeforeUnmount(() => { disposed = true; generation++; clearTimeout(timer); clearInterval(clock); if (session.value?.status !== 'success') release(session.value?.key) })
</script>
