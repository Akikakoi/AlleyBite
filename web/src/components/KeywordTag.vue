<template>
  <span class="keyword-tag" :class="`keyword-tag--${type}`">{{ text }}</span>
</template>

<script setup lang="ts">
withDefaults(
  defineProps<{
    text: string
    type?: 'good' | 'warn' | 'neutral'
  }>(),
  { type: 'neutral' },
)
</script>

<style scoped>
.keyword-tag {
  display: inline-block;
  padding: 2px 10px;
  border-radius: var(--radius-tag);
  font-size: var(--font-hint);
  line-height: 1.8;
  white-space: nowrap;
}

.keyword-tag--good {
  color: var(--color-good);
  background: var(--color-good-soft);
}

.keyword-tag--warn {
  color: var(--color-warn);
  background: var(--color-warn-soft);
}

.keyword-tag--neutral {
  color: var(--color-secondary);
  background: rgba(47, 79, 79, 0.08);
}
</style>