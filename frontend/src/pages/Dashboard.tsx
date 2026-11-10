import { useEffect, useRef, useState } from 'react'
import type { ChangeEvent } from 'react'
import {
  connectGithub,
  createSubmission,
  getGithubStatus,
  listGithubPulls,
  listGithubRepos,
  submitPullRequest,
} from '../api'
import type { GithubPullRequest, GithubRepo, Submission } from '../api'
import { useAuth } from '../AuthContext'
import '../App.css'

const MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024 // 10 MB, per REQUIREMENTS.md functional requirement #3

const SEVERITY_ORDER: Record<string, number> = {
  critical: 0,
  high: 1,
  medium: 2,
  low: 3,
}

function SubmissionResults({ submission }: { submission: Submission }) {
  const handleDownloadReport = () => {
    if (!submission.report) return
    const blob = new Blob([submission.report], { type: 'text/plain' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `${submission.filename.replace(/\.[^.]+$/, '') || 'review'}-report.txt`
    link.click()
    URL.revokeObjectURL(url)
  }

  if (!submission.review) return null

  return (
    <div className="analysis-results">
      <div className="results-header">
        <div>
          <h3>Review Results</h3>
          <p className="results-summary">{submission.review.summary}</p>
        </div>
        <div className="results-header-actions">
          <span
            className="score-badge"
            data-tier={
              submission.review.overall_score >= 80
                ? 'good'
                : submission.review.overall_score >= 50
                  ? 'okay'
                  : 'poor'
            }
          >
            Score: {submission.review.overall_score}/100
          </span>
          {submission.report && (
            <button type="button" className="upload-button" onClick={handleDownloadReport}>
              Download Report
            </button>
          )}
        </div>
      </div>

      {Object.entries(submission.scan_issues ?? {}).map(([path, issue]) => (
        <p key={path} className="scan-issue">
          Static analysis issue for {path}: {issue}
        </p>
      ))}

      <div className="findings-list">
        {[...submission.review.findings]
          .sort((a, b) => (SEVERITY_ORDER[a.severity] ?? 9) - (SEVERITY_ORDER[b.severity] ?? 9))
          .map((finding) => (
            <div key={finding.finding_id} className="finding" data-severity={finding.severity}>
              <div className="finding-header">
                <span className="severity-badge">{finding.severity}</span>
                <h4>{finding.title}</h4>
              </div>

              <p className="finding-location">
                {finding.file_path}:{finding.line}
              </p>

              {finding.source === 'combined' && finding.static_finding_ids.length > 0 && (
                <p className="finding-meta">
                  Confirmed by: {finding.static_finding_ids.join(', ')}
                </p>
              )}

              <p>{finding.explanation}</p>

              {finding.suggested_fix && (
                <p className="finding-fix">Suggested fix: {finding.suggested_fix}</p>
              )}
            </div>
          ))}
      </div>
    </div>
  )
}

interface FileUploadFieldProps {
  label: string
  accept: string
  submissionType: 'patch' | 'diff'
  token: string
}

function FileUploadField({ label, accept, submissionType, token }: FileUploadFieldProps) {
  const [fileName, setFileName] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [uploading, setUploading] = useState(false)
  const [submission, setSubmission] = useState<Submission | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  const handleChange = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (!file) return

    if (file.size > MAX_FILE_SIZE_BYTES) {
      setError(
        `"${file.name}" exceeds the 10 MB size limit and was not uploaded.`,
      )
      setFileName(null)
      setSubmission(null)
      event.target.value = ''
      return
    }

    setError(null)
    setFileName(null)
    setSubmission(null)
    setUploading(true)

    try {
      const result = await createSubmission(submissionType, file, token)
      setFileName(file.name)
      setSubmission(result)
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Request failed.'
      setError(`Upload failed: ${message}`)
    } finally {
      setUploading(false)
      event.target.value = ''
    }
  }

  return (
    <div className="upload-field">
      <button
        type="button"
        className="upload-button"
        onClick={() => inputRef.current?.click()}
        disabled={uploading}
      >
        {uploading ? 'Uploading...' : label}
      </button>
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        onChange={handleChange}
        hidden
      />
      {fileName && <p className="upload-filename">{fileName}</p>}
      {error && <p className="upload-error">{error}</p>}
      {submission && <SubmissionResults submission={submission} />}
    </div>
  )
}

function GithubPanel({ token }: { token: string }) {
  const [connected, setConnected] = useState<boolean | null>(null)
  const [patToken, setPatToken] = useState('')
  const [connecting, setConnecting] = useState(false)
  const [repos, setRepos] = useState<GithubRepo[]>([])
  const [pulls, setPulls] = useState<GithubPullRequest[]>([])
  const [selectedRepo, setSelectedRepo] = useState('')
  const [selectedPullNumber, setSelectedPullNumber] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [submission, setSubmission] = useState<Submission | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    getGithubStatus(token)
      .then((res) => setConnected(res.connected))
      .catch(() => setConnected(false))
  }, [token])

  useEffect(() => {
    if (!connected) return
    listGithubRepos(token)
      .then(setRepos)
      .catch((err) => setError(err instanceof Error ? err.message : 'Failed to load repos.'))
  }, [connected, token])

  const handleConnect = async () => {
    setError(null)
    setConnecting(true)
    try {
      await connectGithub(token, patToken.trim())
      setConnected(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to connect GitHub.')
    } finally {
      setConnecting(false)
    }
  }

  const handleRepoChange = (event: ChangeEvent<HTMLSelectElement>) => {
    const repo = event.target.value
    setSelectedRepo(repo)
    setSelectedPullNumber('')
    setPulls([])
    setSubmission(null)
    if (!repo) return

    listGithubPulls(token, repo)
      .then(setPulls)
      .catch((err) => setError(err instanceof Error ? err.message : 'Failed to load pull requests.'))
  }

  const handleSubmit = async () => {
    const pull = pulls.find((p) => String(p.number) === selectedPullNumber)
    if (!pull) return

    setError(null)
    setSubmission(null)
    setSubmitting(true)
    try {
      const result = await submitPullRequest(token, pull)
      setSubmission(result)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to submit pull request.')
    } finally {
      setSubmitting(false)
    }
  }

  if (connected === null) return null

  if (!connected) {
    return (
      <div className="github-section">
        <label>
          GitHub Personal Access Token
          <input
            type="password"
            value={patToken}
            onChange={(e) => setPatToken(e.target.value)}
            placeholder="ghp_..."
          />
        </label>
        <button
          type="button"
          className="upload-button"
          onClick={handleConnect}
          disabled={connecting || !patToken.trim()}
        >
          {connecting ? 'Connecting...' : 'Connect GitHub'}
        </button>
        {error && <p className="upload-error">{error}</p>}
      </div>
    )
  }

  return (
    <div className="github-section">
      <label>
        Repository
        <select value={selectedRepo} onChange={handleRepoChange}>
          <option value="">Select a repository…</option>
          {repos.map((repo) => (
            <option key={repo.id} value={repo.full_name}>
              {repo.full_name}
            </option>
          ))}
        </select>
      </label>

      {selectedRepo && (
        <label>
          Pull Request
          <select value={selectedPullNumber} onChange={(e) => setSelectedPullNumber(e.target.value)}>
            <option value="">Select a pull request…</option>
            {pulls.map((pull) => (
              <option key={pull.id} value={pull.number}>
                #{pull.number} {pull.title}
              </option>
            ))}
          </select>
        </label>
      )}

      {selectedPullNumber && (
        <button type="button" className="upload-button" onClick={handleSubmit} disabled={submitting}>
          {submitting ? 'Submitting...' : 'Submit for Review'}
        </button>
      )}

      {error && <p className="upload-error">{error}</p>}
      {submission && <SubmissionResults submission={submission} />}
    </div>
  )
}

function Dashboard() {
  const { user, token, logout } = useAuth()

  if (!token) return null

  return (
    <div className="dashboard">
      <header className="dashboard-header">
        <h1>AI Code Review Platform</h1>
        <div className="dashboard-header-actions">
          <span className="username-badge">{user?.username}</span>
          <button type="button" className="logout-button" onClick={logout}>
            Log Out
          </button>
        </div>
      </header>
      <main className="dashboard-main">
        <h2>Welcome back, {user?.username}</h2>
        <p className="dashboard-subtitle">
          Upload the patch or git diff file (Python only) you would like to review, or connect
          GitHub to submit a pull request.
        </p>

        <div className="upload-section">
          <FileUploadField
            label="Upload Patch File"
            accept=".patch,.diff,text/plain"
            submissionType="patch"
            token={token}
          />
          <FileUploadField
            label="Upload Git Diff File"
            accept=".diff,.patch,text/plain"
            submissionType="diff"
            token={token}
          />
          <GithubPanel token={token} />
        </div>
      </main>
    </div>
  )
}

export default Dashboard
