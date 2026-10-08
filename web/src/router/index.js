import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const routes = [
  { path: '/login', name: 'login', component: () => import('../views/LoginView.vue'), meta: { public: true } },
  { path: '/', name: 'dashboard', component: () => import('../views/DashboardView.vue') },
  { path: '/search', name: 'search', component: () => import('../views/SearchView.vue') },
  { path: '/playlists', name: 'playlists', component: () => import('../views/PlaceholderView.vue'), meta: { title: '歌单解析', desc: '粘贴支持解析的音乐平台歌单链接，系统将同步获取完整曲目列表。', phase: 'P2' } },
  { path: '/charts', name: 'charts', component: () => import('../views/PlaceholderView.vue'), meta: { title: '榜单浏览', desc: '浏览 QQ音乐 / 网易云 官方榜单，曲目可直接提交下载。', phase: 'P2' } },
  { path: '/albums', name: 'albums', component: () => import('../views/PlaceholderView.vue'), meta: { title: '专辑管理', desc: '按专辑维度检索元数据、整辑下载与归档入库', phase: 'P2' } },
  { path: '/tasks', name: 'tasks', component: () => import('../views/TasksView.vue') },
  { path: '/library', name: 'library', component: () => import('../views/PlaceholderView.vue'), meta: { title: '媒体库', desc: '浏览与维护媒体库内容。', phase: 'P3' } },
  { path: '/fnos', name: 'fnos', component: () => import('../views/PlaceholderView.vue'), meta: { title: '飞牛歌单', desc: '浏览与维护飞牛音乐歌单。', phase: 'P3' } },
  { path: '/settings', name: 'settings', component: () => import('../views/PlaceholderView.vue'), meta: { title: '系统配置', desc: '管理 MediaMusicService 的全部运行参数，配置对应 config.yaml。', phase: 'P3' } },
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

// 未启用鉴权的服务会直接放行；启用鉴权后任意 API 返回 401，由拦截器带到这里
router.beforeEach((to) => {
  const auth = useAuthStore()
  if (!to.meta.public && !auth.isAuthenticated) {
    return { path: '/login', query: { redirect: to.fullPath } }
  }
})

export default router
