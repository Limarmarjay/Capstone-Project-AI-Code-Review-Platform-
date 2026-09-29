import { afterEach, describe, expect, it, vi } from 'vitest'
import { createSubmission, login, signup } from './api'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('api.ts', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('signup posts credentials and returns the auth payload', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(jsonResponse({ token: 'abc', user: { id: '1', username: 'alice' } }, 201))

    const result = await signup('alice', 'password123')

    expect(fetchMock).toHaveBeenCalledWith(
      'http://127.0.0.1:5002/api/auth/signup',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ username: 'alice', password: 'password123' }),
      }),
    )
    expect(result.token).toBe('abc')
  })

  it('login rejects with the server-provided error message on failure', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      jsonResponse({ error: 'Invalid username or password.' }, 401),
    )

    await expect(login('alice', 'wrong')).rejects.toThrow('Invalid username or password.')
  })

  it('login falls back to a generic message when the error body has no "error" field', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse({}, 500))

    await expect(login('alice', 'wrong')).rejects.toThrow('Request failed (500).')
  })

  it('createSubmission reads the file, sends it as JSON, and includes the auth header', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      jsonResponse(
        {
          _id: 's1',
          submission_type: 'patch',
          filename: 'x.patch',
          content: 'diff',
          status: 'parsed',
          review: {
            submission_id: 's1',
            review_status: 'completed',
            llm_status: 'success',
            overall_score: 90,
            summary: 'Looks fine.',
            findings: [],
          },
          scan_issues: {},
        },
        201,
      ),
    )

    const file = new File(['diff --git a/x.py b/x.py'], 'x.patch', { type: 'text/plain' })
    const result = await createSubmission('patch', file, 'test-token')

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [, options] = fetchMock.mock.calls[0]
    expect(options?.headers).toMatchObject({ Authorization: 'Bearer test-token' })
    const sentBody = JSON.parse(options?.body as string)
    expect(sentBody).toMatchObject({
      submission_type: 'patch',
      filename: 'x.patch',
      content: 'diff --git a/x.py b/x.py',
    })
    expect(result._id).toBe('s1')
    expect(result.review?.overall_score).toBe(90)
  })

  it('createSubmission surfaces the Python-only rejection message from the backend', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      jsonResponse(
        {
          error: 'Only Python (.py) files are supported. Found non-Python file(s): x.js',
          submission: { status: 'blocked' },
        },
        400,
      ),
    )

    const file = new File(['diff --git a/x.js b/x.js'], 'x.diff')
    await expect(createSubmission('diff', file, 'test-token')).rejects.toThrow(
      /Only Python \(\.py\) files are supported/,
    )
  })

  it('createSubmission surfaces an analysis failure message from the backend', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      jsonResponse({ error: 'Analysis failed unexpectedly.' }, 500),
    )

    const file = new File(['diff --git a/x.py b/x.py'], 'x.diff')
    await expect(createSubmission('diff', file, 'test-token')).rejects.toThrow(
      'Analysis failed unexpectedly.',
    )
  })
})
