import { act, renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import { AuthProvider, useAuth } from './AuthContext'

describe('AuthContext', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('starts with no session when localStorage is empty', () => {
    const { result } = renderHook(() => useAuth(), { wrapper: AuthProvider })
    expect(result.current.token).toBeNull()
    expect(result.current.user).toBeNull()
  })

  it('setSession stores the token/user and updates state', () => {
    const { result } = renderHook(() => useAuth(), { wrapper: AuthProvider })

    act(() => {
      result.current.setSession('tok-123', { id: '1', username: 'alice' })
    })

    expect(result.current.token).toBe('tok-123')
    expect(result.current.user).toEqual({ id: '1', username: 'alice' })
    expect(localStorage.getItem('token')).toBe('tok-123')
    expect(JSON.parse(localStorage.getItem('user')!)).toEqual({ id: '1', username: 'alice' })
  })

  it('logout clears the session from state and localStorage', () => {
    const { result } = renderHook(() => useAuth(), { wrapper: AuthProvider })

    act(() => {
      result.current.setSession('tok-123', { id: '1', username: 'alice' })
    })
    act(() => {
      result.current.logout()
    })

    expect(result.current.token).toBeNull()
    expect(result.current.user).toBeNull()
    expect(localStorage.getItem('token')).toBeNull()
    expect(localStorage.getItem('user')).toBeNull()
  })

  it('a fresh provider picks up a session already in localStorage', () => {
    localStorage.setItem('token', 'preexisting-token')
    localStorage.setItem('user', JSON.stringify({ id: '2', username: 'bob' }))

    const { result } = renderHook(() => useAuth(), { wrapper: AuthProvider })

    expect(result.current.token).toBe('preexisting-token')
    expect(result.current.user).toEqual({ id: '2', username: 'bob' })
  })

  it('useAuth throws when used outside an AuthProvider', () => {
    expect(() => renderHook(() => useAuth())).toThrow(
      'useAuth must be used within an AuthProvider',
    )
  })
})
