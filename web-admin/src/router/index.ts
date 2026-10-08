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
    redirect: '/dashboard',
    children: [
      {
        path: 'dashboard',
        name: 'dashboard',
        component: () => import('@/views/DashboardView.vue'),
        meta: { title: '数据看板' },
      },
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
        path: 'ugc',
        name: 'ugc',
        component: () => import('@/views/UgcView.vue'),
        meta: { title: '打卡审核' },
      },
      {
        path: 'audit',
        name: 'audit',
        component: () => import('@/views/AuditView.vue'),
        meta: { title: '审计日志' },
      },
      {
        path: 'users',
        name: 'users',
        component: () => import('@/views/UsersView.vue'),
        meta: { title: '账号管理', roles: ['superadmin'] },
      },
    ],
  },
  { path: '/:pathMatch(.*)*', redirect: '/dashboard' },
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
    return { path: '/dashboard' }
  }
  // RBAC 前端闸（文档 9.5）：角色不符回看板；后端仍有同口径校验兜底
  const roles = to.meta.roles as string[] | undefined
  if (roles && auth.role && !roles.includes(auth.role)) {
    return { path: '/dashboard' }
  }
  return true
})

router.afterEach((to) => {
  const title = (to.meta.title as string) || ''
  document.title = title ? `${title} · ${BASE_TITLE}` : BASE_TITLE
})

export default router