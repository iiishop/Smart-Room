<script setup>
import { RouterLink, RouterView } from 'vue-router'
</script>

<template>
  <div class="app-shell">
    <header class="app-header">
      <div>
        <p class="eyebrow">Local Network Reader</p>
        <RouterLink class="brand" to="/">LocalJM</RouterLink>
      </div>
      <nav class="top-nav" aria-label="Primary">
        <RouterLink to="/">书架</RouterLink>
      </nav>
    </header>

    <main class="app-main">
      <RouterView />
    </main>
  </div>
</template>
