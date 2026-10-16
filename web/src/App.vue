<template>
  <!-- 路由级页面切换：轻淡入淡出（out-in 防两页交叠的布局跳动）；所有视图单根 .page，满足 Transition 前提 -->
  <router-view v-slot="{ Component }">
    <Transition name="page-fade" mode="out-in">
      <component :is="Component" />
    </Transition>
  </router-view>
</template>

<script setup lang="ts"></script>
