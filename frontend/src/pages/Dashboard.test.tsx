import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AuthProvider } from '../AuthContext'
import * as api from '../api'
import Dashboard from './Dashboard'

vi.mock('../api')

function renderDashboard() {
  localStorage.setItem('token', 'test-token')
  localStorage.setItem('user', JSON.stringify({ id: '1', username: 'alice' }))
  return render(
    <AuthProvider>
      <Dashboard />
    </AuthProvider>,
  )
}

async function uploadFile(buttonName: RegExp, fileContent: string, fileName: string) {
  const user = userEvent.setup()
  const button = screen.getAllByRole('button', { name: buttonName })[0]
  const input = button.parentElement!.querySelector('input[type="file"]') as HTMLInputElement
  const file = new File([fileContent], fileName, { type: 'text/plain' })
  await user.upload(input, file)
}

describe('Dashboard upload flow', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.resetAllMocks()
    vi.mocked(api.getGithubStatus).mockResolvedValue({ connected: false })
  })

  it('rejects a file over the 10 MB size limit without calling the API', async () => {
    renderDashboard()
    const oversized = new File([new Uint8Array(10 * 1024 * 1024 + 1)], 'huge.patch')
    const user = userEvent.setup()
    const button = screen.getAllByRole('button', { name: /Upload Patch File/i })[0]
    const input = button.parentElement!.querySelector('input[type="file"]') as HTMLInputElement
    await user.upload(input, oversized)

    expect(await screen.findByText(/exceeds the 10 MB size limit/)).toBeInTheDocument()
    expect(api.createSubmission).not.toHaveBeenCalled()
  })

  it('shows the filename and review results after a single successful upload call', async () => {
    vi.mocked(api.createSubmission).mockResolvedValue({
      _id: 'sub-1',
      submission_type: 'patch',
      filename: 'hello.patch',
      content: 'diff --git a/hello.py b/hello.py',
      status: 'parsed',
      review: {
        submission_id: 'sub-1',
        review_status: 'completed',
        llm_status: 'success',
        overall_score: 95,
        summary: 'Looks good.',
        findings: [],
      },
      scan_issues: {},
    })

    renderDashboard()
    await uploadFile(/Upload Patch File/i, 'diff --git a/hello.py b/hello.py', 'hello.patch')

    await waitFor(() => expect(api.createSubmission).toHaveBeenCalledWith('patch', expect.any(File), 'test-token'))
    expect(api.createSubmission).toHaveBeenCalledTimes(1)
    expect(await screen.findByText('hello.patch')).toBeInTheDocument()
    expect(await screen.findByText(/Score: 95\/100/)).toBeInTheDocument()
  })

  it('shows an upload error when the submission is rejected (e.g. non-Python files)', async () => {
    vi.mocked(api.createSubmission).mockRejectedValue(
      new Error('Only Python (.py) files are supported. Found non-Python file(s): x.js'),
    )

    renderDashboard()
    await uploadFile(/Upload Git Diff File/i, 'diff --git a/x.js b/x.js', 'mixed.diff')

    expect(
      await screen.findByText(/Upload failed: Only Python \(\.py\) files are supported/),
    ).toBeInTheDocument()
  })

  it('shows an upload error when analysis fails server-side after parsing', async () => {
    vi.mocked(api.createSubmission).mockRejectedValue(new Error('Analysis failed unexpectedly.'))

    renderDashboard()
    await uploadFile(/Upload Git Diff File/i, 'diff --git a/x.py b/x.py', 'x.diff')

    expect(await screen.findByText('Upload failed: Analysis failed unexpectedly.')).toBeInTheDocument()
  })

  it('lets the user download the generated report as a .txt file', async () => {
    vi.mocked(api.createSubmission).mockResolvedValue({
      _id: 'sub-3',
      submission_type: 'patch',
      filename: 'risky.patch',
      content: 'diff --git a/risky.py b/risky.py',
      status: 'parsed',
      review: {
        submission_id: 'sub-3',
        review_status: 'completed',
        llm_status: 'success',
        overall_score: 74,
        summary: '3 issue(s): 1 high, 2 low.',
        findings: [],
      },
      scan_issues: {},
      report: 'Overall Score: 74/100\n3 issue(s): 1 high, 2 low.',
    })

    const createObjectURL = vi.fn().mockReturnValue('blob:mock-url')
    const revokeObjectURL = vi.fn()
    vi.stubGlobal('URL', { ...URL, createObjectURL, revokeObjectURL })
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})

    renderDashboard()
    await uploadFile(/Upload Patch File/i, 'diff --git a/risky.py b/risky.py', 'risky.patch')

    const downloadButton = await screen.findByRole('button', { name: /Download Report/i })
    const user = userEvent.setup()
    await user.click(downloadButton)

    expect(createObjectURL).toHaveBeenCalledTimes(1)
    const blobArg = createObjectURL.mock.calls[0][0] as Blob
    expect(blobArg.type).toBe('text/plain')
    expect(await blobArg.text()).toContain('Overall Score: 74/100')
    expect(clickSpy).toHaveBeenCalledTimes(1)
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:mock-url')

    clickSpy.mockRestore()
    vi.unstubAllGlobals()
  })
})

describe('Dashboard GitHub panel', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.resetAllMocks()
  })

  it('shows a token field when GitHub is not connected', async () => {
    vi.mocked(api.getGithubStatus).mockResolvedValue({ connected: false })

    renderDashboard()

    expect(await screen.findByLabelText(/Personal Access Token/i)).toBeInTheDocument()
    expect(api.listGithubRepos).not.toHaveBeenCalled()
  })

  it('connects with a token and then lists repos', async () => {
    vi.mocked(api.getGithubStatus).mockResolvedValue({ connected: false })
    vi.mocked(api.connectGithub).mockResolvedValue({ connected: true })
    vi.mocked(api.listGithubRepos).mockResolvedValue([{ id: 1, full_name: 'alice/project' }])

    renderDashboard()
    const user = userEvent.setup()

    const tokenInput = await screen.findByLabelText(/Personal Access Token/i)
    await user.type(tokenInput, 'ghp_mytoken')
    await user.click(screen.getByRole('button', { name: /Connect GitHub/i }))

    expect(api.connectGithub).toHaveBeenCalledWith('test-token', 'ghp_mytoken')
    expect(await screen.findByLabelText('Repository')).toBeInTheDocument()
    expect(await screen.findByText('alice/project')).toBeInTheDocument()
  })

  it('shows a connect error without marking GitHub as connected', async () => {
    vi.mocked(api.getGithubStatus).mockResolvedValue({ connected: false })
    vi.mocked(api.connectGithub).mockRejectedValue(new Error('That token could not be verified with GitHub.'))

    renderDashboard()
    const user = userEvent.setup()

    const tokenInput = await screen.findByLabelText(/Personal Access Token/i)
    await user.type(tokenInput, 'bad-token')
    await user.click(screen.getByRole('button', { name: /Connect GitHub/i }))

    expect(
      await screen.findByText('That token could not be verified with GitHub.'),
    ).toBeInTheDocument()
    expect(screen.queryByLabelText('Repository')).not.toBeInTheDocument()
  })

  it('lists pull requests once a repo is selected, and submits the chosen PR', async () => {
    vi.mocked(api.getGithubStatus).mockResolvedValue({ connected: true })
    vi.mocked(api.listGithubRepos).mockResolvedValue([{ id: 1, full_name: 'alice/project' }])
    vi.mocked(api.listGithubPulls).mockResolvedValue([
      { id: 10, number: 42, title: 'Fix bug', html_url: 'https://github.com/alice/project/pull/42' },
    ])
    vi.mocked(api.submitPullRequest).mockResolvedValue({
      _id: 'sub-pr-1',
      submission_type: 'pull_request',
      filename: 'https://github.com/alice/project/pull/42',
      content: '',
      status: 'parsed',
      review: {
        submission_id: 'sub-pr-1',
        review_status: 'completed',
        llm_status: 'success',
        overall_score: 88,
        summary: 'Looks fine.',
        findings: [],
      },
      scan_issues: {},
    })

    renderDashboard()
    const user = userEvent.setup()

    const repoSelect = await screen.findByLabelText('Repository')
    await user.selectOptions(repoSelect, 'alice/project')

    const pullSelect = await screen.findByLabelText('Pull Request')
    expect(await screen.findByText('#42 Fix bug')).toBeInTheDocument()
    await user.selectOptions(pullSelect, '42')

    await user.click(screen.getByRole('button', { name: /Submit for Review/i }))

    expect(api.submitPullRequest).toHaveBeenCalledWith(
      'test-token',
      expect.objectContaining({ number: 42 }),
    )
    expect(await screen.findByText(/Score: 88\/100/)).toBeInTheDocument()
  })
})
