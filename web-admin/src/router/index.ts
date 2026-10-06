import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

import { useAuthStore } from '@/store/auth'

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/LoginView.vue'),
    meta: { title: '登录', public: true },
  },
  {
    path: '/',
    component: () => import('@/layouts/AdminLayout.vue'),
    redirect: '/reviews',
    children: [
      {
        path: 'reviews',
        name: 'reviews',
        component: () => import('@/views/ReviewsView.vue'),
        meta: { title: '店铺审核' },
      },
      {
        path: 'restaurants',
        name: 'restaurants',
        component: () => import('@/views/RestaurantsView.vue'),
        meta: { title: '店铺管理' },
      },
      {
        path: 'crawl',
        name: 'crawl',
        component: () => import('@/views/CrawlView.vue'),
        meta: { title: '采集监控' },
      },
      {
        path: 'feedback',
        name: 'feedback',
        component: () => import('@/views/FeedbackView.vue'),
        meta: { title: '反馈工单' },
      },
      {
        path: 'audit',
        name: 'audit',
        component: () => import('@/views/AuditView.vue'),
        meta: { title: '审计日志' },
      },
    ],
  },
  { path: '/:pathMatch(.*)*', redirect: '/reviews' },
]

const BASE_TITLE = '苍蝇馆子 · 管理后台'

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach((to) => {
  const auth = useAuthStore()
  if (!auth.isLoggedIn && !to.meta.public) {
    return { path: '/login', query: { redirect: to.fullPath } }
  }
  if (auth.isLoggedIn && to.path === '/login') {
    return { path: '/reviews' }
  }
  return true
})

router.afterEach((to) => {
  const title = (to.meta.title as string) || ''
  document.title = title ? `${title} · ${BASE_TITLE}` : BASE_TITLE
})

export default router