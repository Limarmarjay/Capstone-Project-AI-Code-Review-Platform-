import { useRef, useState } from 'react'
import type { ChangeEvent } from 'react'
import { createSubmission } from '../api'
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
