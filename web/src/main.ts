import { createPinia } from 'pinia'
import Vant from 'vant'
import { createApp } from 'vue'
import 'vant/lib/index.css'

import '@/styles/theme.css'

import App from './App.vue'
import router from './router'

createApp(App).use(createPinia()).use(router).use(Vant).mount('#app')