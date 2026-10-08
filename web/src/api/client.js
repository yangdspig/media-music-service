import axios from 'axios'
import { useAuthStore } from '../stores/auth'
import { useToastStore } from '../stores/toast'
import router from '../router'

const client = axios.create({
  baseURL: '/api/v1',
  timeout: 120000,
})

client.interceptors.request.use((config) => {
  const auth = useAuthStore()
  if (auth.apiKey) {
    config.headers['X-API-Key'] = auth.apiKey
  }
  return config
})

client.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      const auth = useAuthStore()
      const wasLoggedIn = auth.isAuthenticated
      auth.clear()
      if (router.currentRoute.value.path !== '/login') {
        useToastStore().show('API Key 无效或已失效，请重新登录', 'error')
        router.push({ path: '/login', query: { redirect: router.currentRoute.value.fullPath } })
      } else if (wasLoggedIn) {
        useToastStore().show('API Key 验证失败', 'error')
      }
    }
    return Promise.reject(error)
  },
)

/** 从 axios 错误里提取面向用户的提示文案 */
export function errorMessage(error, fallback = '请求失败，请稍后重试') {
  const detail = error.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail) && detail.length) return detail[0]?.msg || fallback
  if (error.code === 'ECONNABORTED') return '请求超时，请稍后重试'
  if (!error.response) return '无法连接服务，请确认服务已启动'
  return fallback
}

export default client
