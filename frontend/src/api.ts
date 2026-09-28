const API_BASE_URL = 'http://127.0.0.1:5002/api'

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

interface Submission {
  _id: string
  submission_type: 'patch' | 'diff'
  filename: string
  content: string
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

export interface AnalysisResponse {
  review: ReviewOutput
  scan_issues: Record<string, string>
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

export async function analyzeSubmission(
  submissionId: string,
  token: string,
): Promise<AnalysisResponse> {
  const response = await fetch(`${API_BASE_URL}/analyze`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({
      submission_id: submissionId,
    }),
  })

  return parseResponse<AnalysisResponse>(response)
}