<template>
  <div class="loading-state" role="status">
    <van-loading type="spinner" size="28" color="var(--color-primary)" />
    <p class="text-sub loading-state__text">{{ title }}</p>
  </div>
</template>

<script setup lang="ts">
/** 通用加载态：spinner + 文案，替代静态"加载中…" EmptyState（动效审计 P2 尾巴项） */
withDefaults(defineProps<{ title?: string }>(), { title: '加载中…' })
</script>

<style scoped>
.loading-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  padding: 56px 0;
}
</style>
