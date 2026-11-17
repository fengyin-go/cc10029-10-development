import { createRouter, createWebHistory } from 'vue-router'

import Overview from '@/views/Overview.vue'
const Targetsite = () => import('@/views/targetsite/index.vue')
const Selector = () => import('@/views/selector/index.vue')
const Crawler = () => import('@/views/crawler/index.vue')
const Proxy = () => import('@/views/proxy/index.vue')
const Antiblock = () => import('@/views/antiblock/index.vue')
const Schedule = () => import('@/views/schedule/index.vue')
const Extractor = () => import('@/views/extractor/index.vue')
const Validate = () => import('@/views/validate/index.vue')
const Retry = () => import('@/views/retry/index.vue')
const Storage = () => import('@/views/storage/index.vue')
const Dedup = () => import('@/views/dedup/index.vue')
const Quality = () => import('@/views/quality/index.vue')
const Monitor = () => import('@/views/monitor/index.vue')
const Alert = () => import('@/views/alert/index.vue')
const Apigate = () => import('@/views/apigate/index.vue')
const Archive = () => import('@/views/archive/index.vue')
const Template = () => import('@/views/template/index.vue')
const Flowline = () => import('@/views/flowline/index.vue')
const BatchScope = () => import('@/views/batchscope/index.vue')

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: Overview },
    { path: '/targetsite', name: 'targetsite', component: Targetsite },
    { path: '/selector', name: 'selector', component: Selector },
    { path: '/crawler', name: 'crawler', component: Crawler },
    { path: '/proxy', name: 'proxy', component: Proxy },
    { path: '/antiblock', name: 'antiblock', component: Antiblock },
    { path: '/schedule', name: 'schedule', component: Schedule },
    { path: '/extractor', name: 'extractor', component: Extractor },
    { path: '/validate', name: 'validate', component: Validate },
    { path: '/retry', name: 'retry', component: Retry },
    { path: '/storage', name: 'storage', component: Storage },
    { path: '/dedup', name: 'dedup', component: Dedup },
    { path: '/quality', name: 'quality', component: Quality },
    { path: '/monitor', name: 'monitor', component: Monitor },
    { path: '/alert', name: 'alert', component: Alert },
    { path: '/apigate', name: 'apigate', component: Apigate },
    { path: '/archive', name: 'archive', component: Archive },
    { path: '/template', name: 'template', component: Template },
    { path: '/flowline', name: 'flowline', component: Flowline },
    { path: '/batchscope', name: 'batchscope', component: BatchScope },
  ],
})

export default router
