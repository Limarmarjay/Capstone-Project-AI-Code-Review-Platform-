import { useRef, useState } from 'react'
import type { ChangeEvent } from 'react'
import { createSubmission } from '../api'
import type { Submission } from '../api'
import { useAuth } from '../AuthContext'
import '../App.css'

const MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024 // 10 MB, per REQUIREMENTS.md functional requirement #3

const SEVERITY_ORDER: Record<string, number> = {
  critical: 0,
  high: 1,
  medium: 2,
  low: 3,
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

  const handleDownloadReport = () => {
    if (!submission?.report) return
    const blob = new Blob([submission.report], { type: 'text/plain' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `${submission.filename.replace(/\.[^.]+$/, '') || 'review'}-report.txt`
    link.click()
    URL.revokeObjectURL(url)
  }

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
      {submission?.review && (
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
              .sort(
                (a, b) =>
                  (SEVERITY_ORDER[a.severity] ?? 9) -
                  (SEVERITY_ORDER[b.severity] ?? 9),
              )
              .map((finding) => (
                <div key={finding.finding_id} className="finding" data-severity={finding.severity}>
                  <div className="finding-header">
                    <span className="severity-badge">{finding.severity}</span>
                    <h4>{finding.title}</h4>
                  </div>

                  <p className="finding-location">
                    {finding.file_path}:{finding.line}
                  </p>

                  {finding.source === 'combined' &&
                    finding.static_finding_ids.length > 0 && (
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
      )}
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
          Upload the patch or git diff file (Python only) you would like to review.
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
        </div>
      </main>
    </div>
  )
}

export default Dashboard
