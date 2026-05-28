export const books = [
  {
    id: 'dandadan',
    title: '胆大党',
    author: '龙幸伸',
    status: '连载中',
    tags: ['都市', '超自然'],
    description: '高速滚动阅读体验的示例书目，用于串起书架和章节列表。',
    chapter_count: 3,
    cover: 'https://images.unsplash.com/photo-1512820790803-83ca734da794?auto=format&fit=crop&w=900&q=80',
  },
  {
    id: 'blame',
    title: 'BLAME!',
    author: '贰瓶勉',
    status: '已完结',
    tags: ['科幻', '废土'],
    description: '用于验证多本漫画展示、摘要排版和标签样式。',
    chapter_count: 4,
    cover: 'https://images.unsplash.com/photo-1516979187457-637abb4f9353?auto=format&fit=crop&w=900&q=80',
  },
  {
    id: 'witch-hat',
    title: '魔法使的新娘工房',
    author: '白浜鸥',
    status: '连载中',
    tags: ['奇幻', '成长'],
    description: '保留明亮一点的封面卡片，方便后续检验响应式布局。',
    chapter_count: 2,
    cover: 'https://images.unsplash.com/photo-1511108690759-009324a90311?auto=format&fit=crop&w=900&q=80',
  },
]

export const chapters_by_book = {
  dandadan: [
    { id: 'chapter-001', title: '第 1 话: 超自然开场', pages: 28, updated_at: '2026-05-21' },
    { id: 'chapter-002', title: '第 2 话: 都市怪谈继续', pages: 31, updated_at: '2026-05-24' },
    { id: 'chapter-003', title: '第 3 话: 初次联手', pages: 29, updated_at: '2026-05-27' },
  ],
  blame: [
    { id: 'log-001', title: 'Log.1', pages: 42, updated_at: '2026-04-30' },
    { id: 'log-002', title: 'Log.2', pages: 38, updated_at: '2026-05-03' },
    { id: 'log-003', title: 'Log.3', pages: 41, updated_at: '2026-05-10' },
    { id: 'log-004', title: 'Log.4', pages: 44, updated_at: '2026-05-18' },
  ],
  'witch-hat': [
    { id: 'atelier-001', title: '第 1 话: 帽子的秘密', pages: 36, updated_at: '2026-05-08' },
    { id: 'atelier-002', title: '第 2 话: 第一个咒文', pages: 34, updated_at: '2026-05-20' },
  ],
}

export function find_book(book_id) {
  return books.find((book) => book.id === book_id) ?? null
}

export function get_chapters(book_id) {
  return chapters_by_book[book_id] ?? []
}
