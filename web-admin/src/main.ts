import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import { createPinia } from 'pinia'
import { createApp } from 'vue'
import 'element-plus/dist/index.css'

import '@/styles/theme.css'

import App from './App.vue'
import router from './router'

const pinia = createPinia()

createApp(App).use(pinia).use(router).use(ElementPlus, { locale: zhCn }).mount('#app')