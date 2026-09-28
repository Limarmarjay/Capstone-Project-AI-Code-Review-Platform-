import { useRef, useState } from 'react'
import type { ChangeEvent } from 'react'
import { analyzeSubmission, createSubmission } from '../api'
import type { AnalysisResponse } from '../api'
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
  const [phase, setPhase] = useState<'idle' | 'uploading' | 'analyzing'>('idle')
  const [analysis, setAnalysis] = useState<AnalysisResponse | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  const handleChange = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (!file) return

    if (file.size > MAX_FILE_SIZE_BYTES) {
      setError(
        `"${file.name}" exceeds the 10 MB size limit and was not uploaded.`,
      )
      setFileName(null)
      setAnalysis(null)
      event.target.value = ''
      return
    }

    setError(null)
    setFileName(null)
    setAnalysis(null)
    setPhase('uploading')

    let uploaded = false

    try {
      const submission = await createSubmission(
        submissionType,
        file,
        token,
      )

      uploaded = true
      setFileName(file.name)
      setPhase('analyzing')

      const result = await analyzeSubmission(
        submission._id,
        token,
      )

      setAnalysis(result)
    } catch (err) {
      const message =
        err instanceof Error ? err.message : 'Request failed.'

      setError(
        uploaded
          ? `Uploaded, but analysis failed: ${message}`
          : `Upload failed: ${message}`,
      )
    } finally {
      setPhase('idle')
      event.target.value = ''
    }
}


  return (
    <div className="upload-field">
      <button
        type="button"
        className="upload-button"
        onClick={() => inputRef.current?.click()}
        disabled={phase !== 'idle'}
      >
        {phase === 'uploading'
          ? 'Uploading...'
          : phase === 'analyzing'
            ? 'Analyzing...'
            : label}
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
      {analysis && (
        <div className="analysis-results">
          <h3>Review Results</h3>
          <p>Score: {analysis.review.overall_score}/100</p>
          <p>{analysis.review.summary}</p>

          {Object.entries(analysis.scan_issues).map(([path, issue]) => (
            <p key={path}>
              Static analysis issue for {path}: {issue}
            </p>
          ))}

          {[...analysis.review.findings]
            .sort(
              (a, b) =>
                (SEVERITY_ORDER[a.severity] ?? 9) -
                (SEVERITY_ORDER[b.severity] ?? 9),
            )
            .map((finding) => (
              <div key={finding.finding_id}>
                <h4>
                  {finding.severity.toUpperCase()}: {finding.title}
                </h4>

                <p>
                  {finding.file_path}:{finding.line}
                </p>

                {finding.source === 'combined' &&
                  finding.static_finding_ids.length > 0 && (
                    <p>
                      Confirmed by: {finding.static_finding_ids.join(', ')}
                    </p>
                  )}

                <p>{finding.explanation}</p>

                {finding.suggested_fix && (
                  <p>Suggested fix: {finding.suggested_fix}</p>
                )}
              </div>
            ))}
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
        </div>
      </main>
    </div>
  )
}

export default Dashboard
