import { useEffect, useRef, useState } from 'react'
import type { ChangeEvent, CSSProperties } from 'react'
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
import BrandIcon from '../BrandIcon'
import ThemeToggle from '../ThemeToggle'
import '../App.css'

const MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024 // 10 MB, per REQUIREMENTS.md functional requirement #3

const SEVERITY_ORDER: Record<string, number> = {
  critical: 0,
  high: 1,
  medium: 2,
  low: 3,
}

function scoreTier(score: number): 'good' | 'okay' | 'poor' {
  return score >= 80 ? 'good' : score >= 50 ? 'okay' : 'poor'
}

const SEVERITY_LEVELS = ['critical', 'high', 'medium', 'low'] as const

const SEVERITY_DOT: Record<string, string> = {
  critical: 'var(--critical)',
  high: 'var(--high)',
  medium: 'var(--medium)',
  low: 'var(--low)',
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

  const severityCounts = SEVERITY_LEVELS.map(
    (level) => [level, submission.review!.findings.filter((f) => f.severity === level).length] as const,
  ).filter(([, count]) => count > 0)

  return (
    <div className="analysis-results">
      <div className="results-header">
        <div className="results-title">
          <div
            className="score-ring"
            data-tier={scoreTier(submission.review.overall_score)}
            style={{ '--score': submission.review.overall_score } as CSSProperties}
            aria-hidden="true"
          >
            {submission.review.overall_score}
          </div>
          <div>
            <h3>Review Results</h3>
            <p className="results-summary">{submission.review.summary}</p>
          </div>
        </div>
        <div className="results-header-actions">
          <span className="score-badge" data-tier={scoreTier(submission.review.overall_score)}>
            Score: {submission.review.overall_score}/100
          </span>
          {submission.report && (
            <button type="button" className="upload-button" onClick={handleDownloadReport}>
              Download Report
            </button>
          )}
        </div>
      </div>

      {severityCounts.length > 0 && (
        <ul className="severity-summary" aria-label="Findings by severity">
          {severityCounts.map(([level, count]) => (
            <li key={level} style={{ '--dot': SEVERITY_DOT[level] } as CSSProperties}>
              <b>{count}</b> {level}
            </li>
          ))}
        </ul>
      )}

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
  onResult: (submission: Submission) => void
}

function FileUploadField({ label, accept, submissionType, token, onResult }: FileUploadFieldProps) {
  const [fileName, setFileName] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [uploading, setUploading] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  const handleChange = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (!file) return

    if (file.size > MAX_FILE_SIZE_BYTES) {
      setError(
        `"${file.name}" exceeds the 10 MB size limit and was not uploaded.`,
      )
      setFileName(null)
      event.target.value = ''
      return
    }

    setError(null)
    setFileName(null)
    setUploading(true)

    try {
      const result = await createSubmission(submissionType, file, token)
      setFileName(file.name)
      onResult(result)
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
    </div>
  )
}

function GithubPanel({ token, onResult }: { token: string; onResult: (submission: Submission) => void }) {
  const [connected, setConnected] = useState<boolean | null>(null)
  const [patToken, setPatToken] = useState('')
  const [connecting, setConnecting] = useState(false)
  const [repos, setRepos] = useState<GithubRepo[]>([])
  const [pulls, setPulls] = useState<GithubPullRequest[]>([])
  const [selectedRepo, setSelectedRepo] = useState('')
  const [selectedPullNumber, setSelectedPullNumber] = useState('')
  const [submitting, setSubmitting] = useState(false)
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
    if (!repo) return

    listGithubPulls(token, repo)
      .then(setPulls)
      .catch((err) => setError(err instanceof Error ? err.message : 'Failed to load pull requests.'))
  }

  const handleSubmit = async () => {
    const pull = pulls.find((p) => String(p.number) === selectedPullNumber)
    if (!pull) return

    setError(null)
    setSubmitting(true)
    try {
      const result = await submitPullRequest(token, pull)
      onResult(result)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to submit pull request.')
    } finally {
      setSubmitting(false)
    }
  }

  if (connected === null) {
    return (
      <div className="github-section github-loading" role="status" aria-live="polite">
        <span className="skeleton skeleton-label" />
        <span className="skeleton skeleton-input" />
        <span className="skeleton skeleton-button" />
        <span className="sr-only">Checking GitHub connection…</span>
      </div>
    )
  }

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
            autoComplete="off"
            data-1p-ignore
            data-lpignore="true"
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

      <button
        type="button"
        className="link-button"
        onClick={() => {
          setConnected(false)
          setPatToken('')
          setError(null)
        }}
      >
        Use a different token
      </button>
    </div>
  )
}

type TabKey = 'patch' | 'diff' | 'github'

const TABS: { key: TabKey; label: string }[] = [
  { key: 'patch', label: 'Upload Patch' },
  { key: 'diff', label: 'Upload Git Diff' },
  { key: 'github', label: 'Connect GitHub' },
]

function Dashboard() {
  const { user, token, logout } = useAuth()
  const [activeTab, setActiveTab] = useState<TabKey>('patch')
  const [result, setResult] = useState<Submission | null>(null)
  const [githubVisited, setGithubVisited] = useState(false)

  if (!token) return null

  const selectTab = (key: TabKey) => {
    setActiveTab(key)
    if (key === 'github') setGithubVisited(true)
  }

  return (
    <div className="dashboard">
      <header className="dashboard-header">
        <h1>
          <span className="brand-mark" aria-hidden="true">
            <BrandIcon />
          </span>
          AI Code Review Platform
        </h1>
        <div className="dashboard-header-actions">
          <ThemeToggle />
          <span className="username-badge">{user?.username}</span>
          <button type="button" className="logout-button" onClick={logout}>
            Log Out
          </button>
        </div>
      </header>
      <main className="dashboard-main">
        <h2>Welcome back, {user?.username}</h2>
        <p className="dashboard-subtitle">
          Upload the patch or git diff file you would like to review, or connect
          GitHub to submit a pull request.
        </p>

        <div className="tab-bar" role="tablist">
          {TABS.map((tab) => (
            <button
              key={tab.key}
              type="button"
              role="tab"
              aria-selected={activeTab === tab.key}
              className="tab-button"
              onClick={() => selectTab(tab.key)}
            >
              {tab.label}
            </button>
          ))}
        </div>

        <div className="tab-panel">
          {activeTab === 'patch' && (
            <FileUploadField
              label="Upload Patch File"
              accept=".patch,.diff,text/plain"
              submissionType="patch"
              token={token}
              onResult={setResult}
            />
          )}
          {activeTab === 'diff' && (
            <FileUploadField
              label="Upload Git Diff File"
              accept=".diff,.patch,text/plain"
              submissionType="diff"
              token={token}
              onResult={setResult}
            />
          )}
          {githubVisited && (
            <div hidden={activeTab !== 'github'}>
              <GithubPanel token={token} onResult={setResult} />
            </div>
          )}
        </div>

        {result ? (
          <SubmissionResults submission={result} />
        ) : (
          <ol className="steps">
            <li>
              <strong>Add your changes</strong>
              <span>Upload a patch or diff, or pick a pull request.</span>
            </li>
            <li>
              <strong>We analyze them</strong>
              <span>Static checks and an AI reviewer run together.</span>
            </li>
            <li>
              <strong>Review the findings</strong>
              <span>Get a score, ranked issues and suggested fixes.</span>
            </li>
          </ol>
        )}
      </main>
    </div>
  )
}

export default Dashboard
