// VITE_API_URL overrides the backend address at build time. Otherwise production builds use the
// same-origin /api (frontend + backend in one Vercel project) and dev falls back to the local Flask server.
const API_BASE_URL = (import.meta.env.VITE_API_URL as string | undefined)?.replace(/\/+$/, '') || (import.meta.env.PROD ? '/api' : 'http://127.0.0.1:5002/api')

interface AuthResponse {
  token: string
  user: { id: string; username: string }
}

async function parseResponse<T>(response: Response): Promise<T> {
  let data: unknown

  try {
    data = await response.json()
  } catch {
    if (!response.ok) {
      throw new Error(`Request failed (${response.status}).`)
    }

    throw new Error('Server returned an invalid response.')
  }

  if (!response.ok) {
    const message =
      typeof data === 'object' &&
      data !== null &&
      'error' in data &&
      typeof (data as { error?: unknown }).error === 'string'
        ? (data as { error: string }).error
        : `Request failed (${response.status}).`

    throw new Error(message)
  }

  return data as T
}

export function signup(username: string, password: string): Promise<AuthResponse> {
  return fetch(`${API_BASE_URL}/auth/signup`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  }).then((res) => parseResponse<AuthResponse>(res))
}

export function login(username: string, password: string): Promise<AuthResponse> {
  return fetch(`${API_BASE_URL}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  }).then((res) => parseResponse<AuthResponse>(res))
}

export interface ReviewFinding {
  finding_id: string
  source: string
  category: string
  severity: string
  file_path: string
  line: number
  title: string
  explanation: string
  suggested_fix: string
  static_finding_ids: string[]
}

export interface ReviewOutput {
  submission_id: string
  review_status: string
  llm_status: string
  overall_score: number
  summary: string
  findings: ReviewFinding[]
}

export interface Submission {
  _id: string
  submission_type: 'patch' | 'diff' | 'pull_request'
  filename: string
  content: string | null
  status: 'parsed' | 'blocked' | 'error'
  review?: ReviewOutput
  scan_issues?: Record<string, string>
  report?: string
}

export async function createSubmission(
  submissionType: 'patch' | 'diff',
  file: File,
  token: string,
): Promise<Submission> {
  const content = await file.text()
  const response = await fetch(`${API_BASE_URL}/submission`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({
      submission_type: submissionType,
      filename: file.name,
      size: file.size,
      content,
    }),
  })
  return parseResponse<Submission>(response)
}

function authHeaders(token: string) {
  return { Authorization: `Bearer ${token}` }
}

export function connectGithub(token: string, personalAccessToken: string): Promise<{ connected: boolean }> {
  return fetch(`${API_BASE_URL}/github/connect`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders(token) },
    body: JSON.stringify({ token: personalAccessToken }),
  }).then((res) => parseResponse<{ connected: boolean }>(res))
}

export function getGithubStatus(token: string): Promise<{ connected: boolean }> {
  return fetch(`${API_BASE_URL}/github/status`, { headers: authHeaders(token) }).then((res) =>
    parseResponse<{ connected: boolean }>(res),
  )
}

export interface GithubRepo {
  id: number
  full_name: string
}

export function listGithubRepos(token: string): Promise<GithubRepo[]> {
  return fetch(`${API_BASE_URL}/github/repos`, { headers: authHeaders(token) }).then((res) =>
    parseResponse<GithubRepo[]>(res),
  )
}

export interface GithubPullRequest {
  id: number
  number: number
  title: string
  html_url: string
}

export function listGithubPulls(token: string, fullRepoName: string): Promise<GithubPullRequest[]> {
  return fetch(`${API_BASE_URL}/github/repos/${fullRepoName}/pulls`, {
    headers: authHeaders(token),
  }).then((res) => parseResponse<GithubPullRequest[]>(res))
}

export function submitPullRequest(token: string, pull: GithubPullRequest): Promise<Submission> {
  return fetch(`${API_BASE_URL}/submission`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders(token) },
    body: JSON.stringify({ submission_type: 'pull_request', pr_url: pull.html_url }),
  }).then((res) => parseResponse<Submission>(res))
}
