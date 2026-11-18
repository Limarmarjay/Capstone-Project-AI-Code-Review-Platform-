import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  connectGithub,
  getGithubStatus,
  listGithubPulls,
  listGithubRepos,
  login,
  submitPullRequest,
} from './api'

const BASE = 'http://127.0.0.1:5002/api'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('api.ts GitHub endpoints', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('connectGithub posts the PAT with the bearer token', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse({ connected: true }))

    await expect(connectGithub('jwt', 'ghp_x')).resolves.toEqual({ connected: true })

    expect(fetchMock).toHaveBeenCalledWith(`${BASE}/github/connect`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: 'Bearer jwt' },
      body: JSON.stringify({ token: 'ghp_x' }),
    })
  })

  it('getGithubStatus sends only the auth header', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse({ connected: false }))

    await expect(getGithubStatus('jwt')).resolves.toEqual({ connected: false })

    expect(fetchMock).toHaveBeenCalledWith(`${BASE}/github/status`, {
      headers: { Authorization: 'Bearer jwt' },
    })
  })

  it('listGithubRepos returns the repo list', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse([{ id: 1, full_name: 'o/r' }]))
    await expect(listGithubRepos('jwt')).resolves.toEqual([{ id: 1, full_name: 'o/r' }])
  })

  it('listGithubPulls puts the full repo name in the path', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse([]))

    await listGithubPulls('jwt', 'octo/hello')

    expect(fetchMock.mock.calls[0][0]).toBe(`${BASE}/github/repos/octo/hello/pulls`)
  })

  it('submitPullRequest sends the PR html_url as a pull_request submission', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse({ _id: 's1' }, 201))

    await submitPullRequest('jwt', { id: 1, number: 7, title: 't', html_url: 'https://github.com/o/r/pull/7' })

    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe(`${BASE}/submission`)
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({
      submission_type: 'pull_request',
      pr_url: 'https://github.com/o/r/pull/7',
    })
  })

  it('surfaces server error messages from every endpoint', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(() =>
      Promise.resolve(jsonResponse({ error: 'GitHub is not connected.' }, 400)),
    )

    await expect(getGithubStatus('jwt')).rejects.toThrow('GitHub is not connected.')
    await expect(listGithubRepos('jwt')).rejects.toThrow('GitHub is not connected.')
    await expect(listGithubPulls('jwt', 'o/r')).rejects.toThrow('GitHub is not connected.')
  })
})

describe('api.ts response parsing edge cases', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('reports the status code when an error response is not JSON', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('<html>Bad gateway</html>', { status: 502 }))
    await expect(login('a', 'b')).rejects.toThrow('Request failed (502).')
  })

  it('rejects a 200 response that is not JSON', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('not json', { status: 200 }))
    await expect(login('a', 'b')).rejects.toThrow('Server returned an invalid response.')
  })

  it('ignores a non-string "error" field', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse({ error: { nested: true } }, 400))
    await expect(login('a', 'b')).rejects.toThrow('Request failed (400).')
  })

  it('treats a JSON null error body as a generic failure', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse(null, 500))
    await expect(login('a', 'b')).rejects.toThrow('Request failed (500).')
  })

  it('propagates network failures', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new TypeError('Failed to fetch'))
    await expect(login('a', 'b')).rejects.toThrow('Failed to fetch')
  })
})
