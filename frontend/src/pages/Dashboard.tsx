import { useEffect, useRef, useState } from 'react'
import type { ChangeEvent } from 'react'
import {
  createSubmission,
  getGithubConnectUrl,
  getGithubStatus,
  listGithubPulls,
  listGithubRepos,
  submitPullRequest,
} from '../api'
import type { GithubPullRequest, GithubRepo } from '../api'
import { useAuth } from '../AuthContext'
import '../App.css'

const MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024 // 10 MB, per REQUIREMENTS.md functional requirement #3

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
    setUploading(true)
    try {
      await createSubmission(submissionType, file, token)
      setFileName(file.name)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Upload failed.')
    } finally {
      setUploading(false)
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
        {uploading ? 'Uploading…' : label}
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

function GithubConnect({ token }: { token: string }) {
  const [connected, setConnected] = useState<boolean | null>(null)
  const [repos, setRepos] = useState<GithubRepo[]>([])
  const [pulls, setPulls] = useState<GithubPullRequest[]>([])
  const [selectedRepo, setSelectedRepo] = useState('')
  const [selectedPullNumber, setSelectedPullNumber] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitted, setSubmitted] = useState(false)

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    if (params.has('github_connected') || params.has('github_error')) {
      window.history.replaceState({}, '', window.location.pathname)
    }

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
    try {
      await submitPullRequest(token, selectedRepo, pull)
      setSubmitted(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to submit pull request.')
    }
  }

  if (connected === null) return null

  if (!connected) {
    return (
      <div className="upload-field">
        <a className="upload-button" href={getGithubConnectUrl(token)}>
          Connect GitHub
        </a>
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
          <select
            value={selectedPullNumber}
            onChange={(e) => setSelectedPullNumber(e.target.value)}
          >
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
        <button type="button" className="upload-button" onClick={handleSubmit}>
          Submit for Review
        </button>
      )}

      {submitted && <p className="upload-filename">Pull request submitted for review.</p>}
      {error && <p className="upload-error">{error}</p>}
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
          <span>{user?.username}</span>
          <button type="button" onClick={logout}>
            Log Out
          </button>
        </div>
      </header>
      <main className="dashboard-main">
        <h2>Welcome 👋</h2>
        <p>Enter a file you would like to review.</p>

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
          <GithubConnect token={token} />
        </div>
      </main>
    </div>
  )
}

export default Dashboard
