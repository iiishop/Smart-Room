<script setup>
import { computed } from 'vue'
import { RouterLink } from 'vue-router'

import { find_book, get_chapters } from '../data/library'

const props = defineProps({
  bookId: {
    type: String,
    required: true,
  },
})

const book = computed(() => find_book(props.bookId))
const chapters = computed(() => get_chapters(props.bookId))
</script>

<template>
  <section class="page-stack">
    <RouterLink class="secondary-link" to="/">返回书架</RouterLink>

    <div v-if="book" class="chapter-hero">
      <div>
        <p class="eyebrow">Chapters</p>
        <h1>{{ book.title }}</h1>
        <p class="hero-copy">{{ book.description }}</p>
      </div>
      <div class="chapter-summary">
        <span>{{ chapters.length }} 个章节</span>
        <span>{{ book.author }}</span>
      </div>
    </div>

    <div v-if="book" class="chapter-list">
      <article v-for="chapter in chapters" :key="chapter.id" class="chapter-card">
        <div>
          <p class="chapter-index">{{ chapter.id }}</p>
          <h2>{{ chapter.title }}</h2>
          <p class="chapter-meta">
            {{ chapter.pages }} 页 · 更新于 {{ chapter.updated_at }}
          </p>
        </div>
        <RouterLink :to="`/books/${book.id}/chapters/${chapter.id}/read`" class="primary-link">
          进入阅读器
        </RouterLink>
      </article>
    </div>

    <div v-else class="empty-state">
      <h1>未找到对应漫画</h1>
      <p>请从书架重新进入章节列表。</p>
    </div>
  </section>
</template>
