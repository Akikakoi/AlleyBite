import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

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
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

const BASE_TITLE = '苍蝇馆子美食发现器'

const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior: () => ({ top: 0 }),
})

router.afterEach((to) => {
  const title = (to.meta.title as string) || ''
  document.title = title ? `${title} · ${BASE_TITLE}` : BASE_TITLE
})

export default router