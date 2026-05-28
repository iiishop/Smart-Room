import test from 'node:test'
import assert from 'node:assert/strict'

import { books, find_book, get_chapters } from '../src/data/library.js'

test('find_book returns the requested book', () => {
  const book = find_book('dandadan')

  assert.ok(book)
  assert.equal(book.title, '胆大党')
})

test('find_book returns null for an unknown book id', () => {
  assert.equal(find_book('missing-book'), null)
})

test('get_chapters returns chapter data for a known book', () => {
  const chapters = get_chapters('blame')

  assert.equal(chapters.length, 4)
  assert.equal(chapters[0].id, 'log-001')
})

test('mock library exposes chapter counts for every book card', () => {
  const chapter_total = books.reduce((sum, book) => sum + book.chapter_count, 0)

  assert.equal(chapter_total, 9)
})
