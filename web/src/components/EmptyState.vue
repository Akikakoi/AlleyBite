<template>
  <div class="empty-state">
    <svg
      class="empty-state__art"
      viewBox="0 0 120 96"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
    >
      <ellipse cx="60" cy="82" rx="42" ry="6" fill="#2F4F4F" opacity="0.08" />
      <path
        d="M22 52h76c0 18-14 30-38 30S22 70 22 52Z"
        fill="#FF6B35"
        opacity="0.16"
        stroke="#FF6B35"
        stroke-width="2.5"
      />
      <path d="M30 46h60" stroke="#2F4F4F" stroke-width="3" stroke-linecap="round" />
      <path
        d="M46 34c-6-6 2-10 0-16M60 32c-6-6 2-10 0-16M74 34c-6-6 2-10 0-16"
        stroke="#2F4F4F"
        stroke-width="2.5"
        stroke-linecap="round"
        opacity="0.5"
      />
    </svg>
    <p class="empty-state__title">{{ title }}</p>
    <p v-if="desc" class="text-sub empty-state__desc">{{ desc }}</p>
    <div v-if="showAction" class="empty-state__action">
      <slot name="action" />
    </div>
  </div>
</template>

<script setup lang="ts">
withDefaults(
  defineProps<{
    title: string
    desc?: string
    showAction?: boolean
  }>(),
  { desc: '', showAction: true },
)
</script>

<style scoped>
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 48px 16px;
  text-align: center;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-card);
}

.empty-state__art {
  width: 120px;
  height: 96px;
}

.empty-state__title {
  margin: 4px 0 0;
  font-size: var(--font-title);
  font-weight: 600;
}

.empty-state__desc {
  margin: 0;
  max-width: 320px;
}

.empty-state__action {
  margin-top: 12px;
}
</style>