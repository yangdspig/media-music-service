<template>
  <div class="min-h-screen flex items-center justify-center bg-background px-4">
    <div class="w-full max-w-sm rounded-lg border border-border bg-card p-8" style="box-shadow: var(--mm-shadow-md);">
      <div class="flex flex-col items-center mb-6">
        <span class="inline-flex items-center justify-center w-12 h-12 rounded-lg bg-primary text-primary-foreground mb-3">
          <Music4 class="w-6 h-6" />
        </span>
        <h1 class="text-lg font-semibold text-foreground">MediaMusicService</h1>
        <p class="mt-1 text-sm text-muted-foreground">输入 API Key 登录管理后台</p>
      </div>

      <form class="space-y-4" @submit.prevent="submit">
        <div>
          <label class="text-xs font-medium text-muted-foreground block mb-1.5" for="api-key">API Key</label>
          <input
            id="api-key"
            v-model.trim="apiKey"
            type="password"
            placeholder="请输入 config.yaml 中配置的 api_key"
            class="w-full h-10 px-3 rounded-md border border-input bg-background text-sm focus:outline-none focus:ring-2 focus:ring-ring focus:border-transparent placeholder:text-muted-foreground"
            autocomplete="off"
          />
        </div>
        <p v-if="error" class="text-xs" style="color: var(--state-error);">{{ error }}</p>
        <button
          type="submit"
          class="btn-lift w-full h-10 rounded-md bg-primary text-primary-foreground text-sm font-medium flex items-center justify-center gap-2"
          :disabled="loading"
        >
          <Loader2 v-if="loading" class="w-4 h-4 animate-spin" />
          {{ loading ? '验证中...' : '登录' }}
        </button>
        <button
          type="button"
          class="w-full h-10 rounded-md border border-border text-sm text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
          :disabled="loading"
          @click="enterWithoutKey"
        >
          服务未启用鉴权？直接进入
        </button>
      </form>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Music4, Loader2 } from 'lucide-vue-next'
import client from '../api/client'
import { useAuthStore } from '../stores/auth'
import { useToastStore } from '../stores/toast'

const auth = useAuthStore()
const toast = useToastStore()
const route = useRoute()
const router = useRouter()

const apiKey = ref('')
const loading = ref(false)
const error = ref('')

function redirectTarget() {
  const target = route.query.redirect
  return typeof target === 'string' && target.startsWith('/') ? target : '/'
}

async function verify(key) {
  loading.value = true
  error.value = ''
  auth.setKey(key)
  try {
    // 用 /sources 校验 key 是否有效；未启用鉴权的服务任何 key 都会放行
    await client.get('/sources')
    toast.show('登录成功', 'success')
    router.push(redirectTarget())
  } catch (e) {
    if (e.response?.status === 401) {
      error.value = 'API Key 不正确，请检查后重试'
    } else {
      error.value = '无法连接服务，请确认服务已启动'
    }
    auth.clear()
  } finally {
    loading.value = false
  }
}

function submit() {
  if (!apiKey.value) {
    error.value = '请输入 API Key'
    return
  }
  verify(apiKey.value)
}

function enterWithoutKey() {
  // 服务未启用鉴权时不带 key 直接进入；若服务实际开了鉴权，后续 401 会被拦截回来
  auth.clear()
  router.push(redirectTarget())
}
</script>
