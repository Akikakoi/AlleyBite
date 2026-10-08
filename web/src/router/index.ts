import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

import { getToken } from '@/utils/token'

const routes: RouteRecordRaw[] = [
  {
    path: '/',
    name: 'home',
    component: () => import('@/views/home/HomeView.vue'),
    meta: { title: '找苍蝇馆子' },
  },
  {
    path: '/rank',
    name: 'rank',
    component: () => import('@/views/rank/RankView.vue'),
    meta: { title: '榜单' },
  },
  {
    path: '/map',
    name: 'map',
    component: () => import('@/views/map/MapView.vue'),
    meta: { title: '地图模式' },
  },
  {
    path: '/detail/:id',
    name: 'detail',
    component: () => import('@/views/detail/DetailView.vue'),
    meta: { title: '店铺详情' },
  },
  {
    path: '/source',
    name: 'source',
    component: () => import('@/views/source-notice/SourceNoticeView.vue'),
    meta: { title: '来源提示' },
  },
  {
    path: '/mine',
    name: 'mine',
    component: () => import('@/views/mine/MineView.vue'),
    meta: { title: '我的', requiresAuth: true },
  },
  {
    path: '/mine/favorites',
    name: 'favorites',
    component: () => import('@/views/mine/FavoritesView.vue'),
    meta: { title: '我的收藏', requiresAuth: true },
  },
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/mine/LoginView.vue'),
    meta: { title: '登录' },
  },
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

const BASE_TITLE = '苍蝇馆子美食发现器'

const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior: () => ({ top: 0 }),
})

// 用户态路由守卫（V2.0 账号体系）：未登录跳登录页，登录后回跳原页
router.beforeEach((to) => {
  if (to.meta.requiresAuth && !getToken()) {
    return { path: '/login', query: { redirect: to.fullPath } }
  }
})

router.afterEach((to) => {
  const title = (to.meta.title as string) || ''
  document.title = title ? `${title} · ${BASE_TITLE}` : BASE_TITLE
})

export default router