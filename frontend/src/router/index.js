import { createRouter, createWebHashHistory } from 'vue-router'

import BookshelfPage from '../views/BookshelfPage.vue'
import ChapterListPage from '../views/ChapterListPage.vue'
import ReaderPage from '../views/ReaderPage.vue'

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    {
      path: '/',
      name: 'bookshelf',
      component: BookshelfPage,
    },
    {
      path: '/books/:bookId/chapters',
      name: 'chapters',
      component: ChapterListPage,
      props: true,
    },
    {
      path: '/books/:bookId/chapters/:chapterId/read',
      name: 'reader',
      component: ReaderPage,
      props: true,
    },
  ],
})

export default router
