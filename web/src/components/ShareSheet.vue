<template>
  <van-popup
    :show="show"
    round
    position="bottom"
    :style="{ padding: '20px 16px 24px' }"
    @update:show="(value: boolean) => emit('update:show', value)"
  >
    <h2 class="share__title">分享</h2>
    <p class="text-sub share__hint">{{ title }}</p>

    <div class="share__preview">
      <van-loading v-if="loading" size="22px">生成分享图…</van-loading>
      <img v-else-if="previewUrl" :src="previewUrl" class="share__img" alt="分享图预览" />
      <p v-else class="text-sub">分享图生成失败，可用链接分享</p>
    </div>

    <div class="share__actions">
      <van-button
        round
        type="primary"
        block
        :disabled="!blob"
        :loading="saving"
        @click="onSave"
      >
        保存图片
      </van-button>
      <van-button round plain type="primary" block :disabled="!blob" @click="onShareImage">
        系统分享图片
      </van-button>
      <van-button round plain block @click="onCopyLink">复制链接</van-button>
    </div>
  </van-popup>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { showToast } from 'vant'

import { downloadBlob, shareOrDownloadImage } from '@/utils/shareImage'
import { shareLink } from '@/utils/share'

const props = defineProps<{
  show: boolean
  title: string
  text?: string
  url: string
  filename: string
  poster: () => Promise<Blob>
}>()

const emit = defineEmits<{ (e: 'update:show', value: boolean): void }>()

const loading = ref(false)
const saving = ref(false)
const blob = ref<Blob | null>(null)
const previewUrl = ref('')

function revoke() {
  if (previewUrl.value) {
    URL.revokeObjectURL(previewUrl.value)
    previewUrl.value = ''
  }
}

async function generate() {
  revoke()
  blob.value = null
  loading.value = true
  try {
    const result = await props.poster()
    blob.value = result
    previewUrl.value = URL.createObjectURL(result)
  } catch {
    showToast('分享图生成失败')
  } finally {
    loading.value = false
  }
}

watch(
  () => props.show,
  (visible) => {
    if (visible) generate()
    else revoke()
  },
)

function onSave() {
  if (!blob.value) return
  saving.value = true
  try {
    downloadBlob(blob.value, props.filename)
    showToast('分享图已保存')
  } finally {
    saving.value = false
  }
}

async function onShareImage() {
  if (!blob.value) return
  await shareOrDownloadImage(blob.value, props.filename, props.text || props.title)
}

async function onCopyLink() {
  await shareLink({ title: props.title, text: props.text, url: props.url })
}
</script>

<style scoped>
.share__title {
  margin: 0;
  font-size: 17px;
  font-weight: 700;
  color: var(--color-secondary);
}

.share__hint {
  margin: 4px 0 14px;
}

.share__preview {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 200px;
  padding: 12px;
  margin-bottom: 16px;
  background: var(--color-bg);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-card);
}

.share__img {
  width: 100%;
  max-width: 260px;
  border-radius: 12px;
  box-shadow: var(--shadow-card);
}

.share__actions {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
</style>