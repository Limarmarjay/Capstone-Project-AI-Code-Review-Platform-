import { screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('./api')

describe('main.tsx entry point', () => {
  beforeEach(() => {
    localStorage.clear()
    document.body.innerHTML = '<div id="root"></div>'
    window.history.pushState({}, '', '/')
    vi.resetModules()
  })

  afterEach(() => {
    document.body.innerHTML = ''
  })

  it('mounts the app into #root with all providers (unauthenticated -> login screen)', async () => {
    await import('./main')
    expect(await screen.findByRole('heading', { name: 'Log In' })).toBeInTheDocument()
    expect(document.documentElement).toHaveAttribute('data-theme')
  })
})
