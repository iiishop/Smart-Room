<script setup>
import { computed } from 'vue'
import { RouterLink } from 'vue-router'

import { find_book, get_chapters } from '../data/library'

const props = defineProps({
  bookId: {
    type: String,
    required: true,
  },
  chapterId: {
    type: String,
    required: true,
  },
})

const book = computed(() => find_book(props.bookId))
const chapter = computed(() =>
  get_chapters(props.bookId).find((entry) => entry.id === props.chapterId) ?? null,
)
</script>

<template>
  <section class="page-stack">
    <RouterLink :to="`/books/${bookId}/chapters`" class="secondary-link">
      返回章节列表
    </RouterLink>

    <div v-if="book && chapter" class="reader-shell">
      <div class="reader-copy">
        <p class="eyebrow">Reader</p>
        <h1>{{ chapter.title }}</h1>
        <p>
          阅读器路由已接通，下一阶段在这里接入图片流和 Intersection Observer 懒加载。
        </p>
      </div>
      <div class="reader-placeholder">
        <div class="reader-frame">
          <span>{{ book.title }}</span>
          <strong>{{ chapter.pages }} Pages</strong>
        </div>
        <p>
          预留 `/api/books/:id/chapters/:id/pages` 或图片静态目录映射接入点。
        </p>
      </div>
    </div>

    <div v-else class="empty-state">
      <h1>章节不存在</h1>
      <p>当前路由参数未匹配到本地数据。</p>
    </div>
  </section>
</template>
