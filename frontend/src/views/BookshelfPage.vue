<script setup>
import { RouterLink } from 'vue-router'

import { books } from '../data/library'
</script>

<template>
  <section class="page-stack">
    <div class="hero-card">
      <div>
        <p class="eyebrow">Bookshelf</p>
        <h1>本地漫画书架</h1>
        <p class="hero-copy">
          当前先用本地假数据跑通页面结构。后续后端 `/api/books` 就位后，只需要把数据源切到接口。
        </p>
      </div>
      <div class="hero-stats">
        <div>
          <strong>{{ books.length }}</strong>
          <span>作品</span>
        </div>
        <div>
          <strong>{{ books.reduce((sum, book) => sum + book.chapter_count, 0) }}</strong>
          <span>章节</span>
        </div>
      </div>
    </div>

    <div class="section-heading">
      <div>
        <p class="eyebrow">Collection</p>
        <h2>继续阅读</h2>
      </div>
    </div>

    <div class="bookshelf-grid">
      <article v-for="book in books" :key="book.id" class="book-card">
        <div class="book-cover-wrap">
          <img :src="book.cover" :alt="`${book.title} cover`" class="book-cover" loading="lazy" />
        </div>
        <div class="book-body">
          <div class="book-meta">
            <span class="status-pill">{{ book.status }}</span>
            <span>{{ book.chapter_count }} 章</span>
          </div>
          <h3>{{ book.title }}</h3>
          <p class="author-line">{{ book.author }}</p>
          <p class="book-description">{{ book.description }}</p>
          <ul class="tag-list" aria-label="book tags">
            <li v-for="tag in book.tags" :key="tag">{{ tag }}</li>
          </ul>
          <RouterLink :to="`/books/${book.id}/chapters`" class="primary-link">
            查看章节
          </RouterLink>
        </div>
      </article>
    </div>
  </section>
</template>
