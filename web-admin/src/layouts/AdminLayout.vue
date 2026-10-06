<template>
  <el-container class="admin-shell">
    <el-aside width="220px" class="admin-aside">
      <div class="admin-brand">
        <span class="admin-brand__mark">巷</span>
        <div class="admin-brand__text">
          <div class="admin-brand__title">苍蝇馆子</div>
          <div class="admin-brand__sub">管理后台</div>
        </div>
      </div>
      <el-menu :default-active="activeMenu" class="admin-menu" router>
        <el-menu-item index="/reviews">
          <el-icon><List /></el-icon>
          <span>店铺审核</span>
        </el-menu-item>
        <el-menu-item index="/restaurants">
          <el-icon><Shop /></el-icon>
          <span>店铺管理</span>
        </el-menu-item>
        <el-menu-item index="/crawl">
          <el-icon><Monitor /></el-icon>
          <span>采集监控</span>
        </el-menu-item>
        <el-menu-item index="/feedback">
          <el-icon><ChatDotRound /></el-icon>
          <span>反馈工单</span>
        </el-menu-item>
        <el-menu-item index="/audit">
          <el-icon><Document /></el-icon>
          <span>审计日志</span>
        </el-menu-item>
      </el-menu>
    </el-aside>

    <el-container>
      <el-header class="admin-header">
        <div class="admin-header__title">{{ pageTitle }}</div>
        <div class="admin-header__right">
          <el-tag v-if="auth.role" type="warning" effect="plain">{{ auth.role }}</el-tag>
          <span class="admin-header__user">{{ auth.username || '未命名账号' }}</span>
          <el-button text type="primary" @click="handleLogout">退出登录</el-button>
        </div>
      </el-header>
      <el-main class="admin-main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup lang="ts">
import { ChatDotRound, Document, List, Monitor, Shop } from '@element-plus/icons-vue'
import { ElMessageBox } from 'element-plus'
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { useAuthStore } from '@/store/auth'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()

const activeMenu = computed(() => route.path)
const pageTitle = computed(() => (route.meta.title as string) || '管理后台')

async function handleLogout() {
  try {
    await ElMessageBox.confirm('确认退出当前登录账号？', '退出登录', {
      type: 'warning',
      confirmButtonText: '退出',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  auth.clear()
  router.replace('/login')
}
</script>

<style scoped>
.admin-shell {
  height: 100vh;
}

.admin-aside {
  background: #2f4f4f;
  display: flex;
  flex-direction: column;
}

.admin-brand {
  display: flex;
  align-items: center;
  gap: 10px;
  height: 60px;
  padding: 0 16px;
  color: #fff;
}

.admin-brand__mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border-radius: 8px;
  background: #ff6b35;
  font-size: 18px;
  font-weight: 700;
}

.admin-brand__title {
  font-size: 15px;
  font-weight: 600;
  line-height: 1.2;
}

.admin-brand__sub {
  font-size: 12px;
  color: rgba(255, 255, 255, 0.6);
}

.admin-menu {
  border-right: none;
  background: transparent;
  --el-menu-text-color: rgba(255, 255, 255, 0.75);
  --el-menu-hover-text-color: #fff;
  --el-menu-hover-bg-color: rgba(255, 255, 255, 0.08);
  --el-menu-active-color: #fff;
}

.admin-menu :deep(.el-menu-item.is-active) {
  background: #ff6b35;
}

.admin-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: #fff;
  border-bottom: 1px solid #ebeef5;
}

.admin-header__title {
  font-size: 16px;
  font-weight: 600;
  color: #2f4f4f;
}

.admin-header__right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.admin-header__user {
  font-size: 14px;
  color: #2f4f4f;
}

.admin-main {
  padding: 0;
  background: #f6f7f8;
  overflow: auto;
}
</style>