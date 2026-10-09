import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const routes = [
  { path: '/login', name: 'login', component: () => import('../views/LoginView.vue'), meta: { public: true } },
  { path: '/', name: 'dashboard', component: () => import('../views/DashboardView.vue') },
  { path: '/search', name: 'search', component: () => import('../views/SearchView.vue') },
  { path: '/playlists', name: 'playlists', component: () => import('../views/PlaylistsView.vue') },
  { path: '/charts', name: 'charts', component: () => import('../views/ChartsView.vue') },
  { path: '/albums', name: 'albums', component: () => import('../views/AlbumsView.vue') },
  { path: '/albums/:collectionId', name: 'album-detail', component: () => import('../views/AlbumsView.vue') },
  { path: '/tasks', name: 'tasks', component: () => import('../views/TasksView.vue') },
  { path: '/library', name: 'library', component: () => import('../views/LibraryView.vue') },
  { path: '/fnos', name: 'fnos', component: () => import('../views/FnosView.vue') },
  { path: '/fnos/:name', name: 'fnos-detail', component: () => import('../views/FnosView.vue') },
  { path: '/settings', name: 'settings', component: () => import('../views/SettingsView.vue') },
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
