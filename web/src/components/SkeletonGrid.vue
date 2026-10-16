<template>
  <div class="skeleton-grid list-grid" aria-hidden="true">
    <div v-for="i in 6" :key="i" class="card skeleton-card">
      <div class="skeleton-bone skeleton-card__rank"></div>
      <div class="skeleton-card__body">
        <div class="skeleton-bone skeleton-card__name"></div>
        <div class="skeleton-bone skeleton-card__meta"></div>
        <div class="skeleton-bone skeleton-card__tag"></div>
        <div class="skeleton-bone skeleton-card__dish"></div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/** 榜单加载骨架：对齐 ShopCard 布局（排名圆标 + 名称/口碑行/标签/推荐菜），呼吸微光 */
</script>

<style scoped>
/* 循环微光属"氛围动画"，不受 UI <300ms 约束；reduce 下全局降级块把
   animation-duration 压到 ≈0，退化为静态灰块，无位移无闪烁 */
.skeleton-bone {
  background: linear-gradient(
    90deg,
    rgba(38, 34, 31, 0.06) 25%,
    rgba(38, 34, 31, 0.12) 37%,
    rgba(38, 34, 31, 0.06) 63%
  );
  background-size: 400% 100%;
  animation: skeleton-shimmer 1.2s linear infinite;
  border-radius: 6px;
}

@keyframes skeleton-shimmer {
  from {
    background-position: 100% 50%;
  }
  to {
    background-position: 0 50%;
  }
}

.skeleton-card {
  display: flex;
  gap: 12px;
  padding: 16px;
}

.skeleton-card__rank {
  flex: none;
  width: 28px;
  height: 28px;
  border-radius: 50%;
}

.skeleton-card__body {
  flex: 1;
  min-width: 0;
}

.skeleton-card__name {
  width: 55%;
  height: 17px;
}

.skeleton-card__meta {
  width: 75%;
  height: 12px;
  margin-top: 10px;
}

.skeleton-card__tag {
  width: 45%;
  height: 20px;
  margin-top: 12px;
  border-radius: 999px;
}

.skeleton-card__dish {
  width: 65%;
  height: 12px;
  margin-top: 12px;
}
</style>
