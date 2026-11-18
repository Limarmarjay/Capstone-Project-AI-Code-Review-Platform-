import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AuthProvider } from '../AuthContext'
import { ThemeProvider } from '../ThemeContext'
import * as api from '../api'
import type { ReviewFinding, Submission } from '../api'
import Dashboard from './Dashboard'

vi.mock('../api')

function renderDashboard() {
  localStorage.setItem('token', 'test-token')
  localStorage.setItem('user', JSON.stringify({ id: '1', username: 'alice' }))
  return render(
    <ThemeProvider>
      <AuthProvider>
        <Dashboard />
      </AuthProvider>
    </ThemeProvider>,
  )
}

function finding(overrides: Partial<ReviewFinding> = {}): ReviewFinding {
  return {
    finding_id: 'f1',
    source: 'llm',
    category: 'security',
    severity: 'high',
    file_path: 'a.py',
    line: 3,
    title: 'Shell injection',
    explanation: 'User input reaches a shell.',
    suggested_fix: 'Pass a list of args.',
    static_finding_ids: [],
    ...overrides,
  }
}

function submission(overrides: Partial<Submission> = {}, score = 90, findings: ReviewFinding[] = []): Submission {
  return {
    _id: 's1',
    submission_type: 'patch',
    filename: 'my.review.patch',
    content: 'diff',
    status: 'parsed',
    review: {
      submission_id: 's1',
      review_status: 'completed',
      llm_status: 'success',
      overall_score: score,
      summary: 'Summary text.',
      findings,
    },
    scan_issues: {},
    report: 'REPORT BODY',
    ...overrides,
  }
}

async function upload(label: RegExp, name = 'x.patch', content = 'diff') {
  const user = userEvent.setup()
  const button = screen.getAllByRole('button', { name: label })[0]
  const input = button.parentElement!.querySelector('input[type="file"]') as HTMLInputElement
  await user.upload(input, new File([content], name, { type: 'text/plain' }))
}

describe('Dashboard results rendering', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.resetAllMocks()
    vi.mocked(api.getGithubStatus).mockResolvedValue({ connected: false })
  })

  it.each([
    [95, 'good'],
    [80, 'good'],
    [79, 'okay'],
    [50, 'okay'],
    [49, 'poor'],
    [0, 'poor'],
  ])('score %i gets the "%s" tier', async (score, tier) => {
    vi.mocked(api.createSubmission).mockResolvedValue(submission({}, score))
    renderDashboard()
    await upload(/Upload Patch File/i)
    expect(await screen.findByText(`Score: ${score}/100`)).toHaveAttribute('data-tier', tier)
  })

  it('orders findings by severity (critical first, unknown last)', async () => {
    vi.mocked(api.createSubmission).mockResolvedValue(
      submission({}, 40, [
        finding({ finding_id: 'a', severity: 'low', title: 'low one' }),
        finding({ finding_id: 'b', severity: 'mystery', title: 'unknown one' }),
        finding({ finding_id: 'c', severity: 'critical', title: 'critical one' }),
        finding({ finding_id: 'd', severity: 'medium', title: 'medium one' }),
        finding({ finding_id: 'e', severity: 'high', title: 'high one' }),
      ]),
    )
    renderDashboard()
    await upload(/Upload Patch File/i)
    await screen.findByText('critical one')
    const titles = screen.getAllByRole('heading', { level: 4 }).map((h) => h.textContent)
    expect(titles).toEqual(['critical one', 'high one', 'medium one', 'low one', 'unknown one'])
  })

  it('shows location, explanation and suggested fix; omits an empty fix', async () => {
    vi.mocked(api.createSubmission).mockResolvedValue(
      submission({}, 70, [
        finding({ finding_id: 'a' }),
        finding({ finding_id: 'b', title: 'No fix', suggested_fix: '' }),
      ]),
    )
    renderDashboard()
    await upload(/Upload Patch File/i)
    expect(await screen.findAllByText('a.py:3')).toHaveLength(2)
    expect(screen.getAllByText('User input reaches a shell.')).toHaveLength(2)
    expect(screen.getAllByText(/Suggested fix:/)).toHaveLength(1)
  })

  it('shows "Confirmed by" only for combined findings with static ids', async () => {
    vi.mocked(api.createSubmission).mockResolvedValue(
      submission({}, 60, [
        finding({ finding_id: 'a', source: 'combined', static_finding_ids: ['bandit:B602:a.py:3', 'ruff:E1'] }),
        finding({ finding_id: 'b', source: 'combined', static_finding_ids: [], title: 'combined, no ids' }),
        finding({ finding_id: 'c', source: 'llm', static_finding_ids: ['x'], title: 'llm only' }),
      ]),
    )
    renderDashboard()
    await upload(/Upload Patch File/i)
    const confirmed = await screen.findAllByText(/Confirmed by:/)
    expect(confirmed).toHaveLength(1)
    expect(confirmed[0]).toHaveTextContent('bandit:B602:a.py:3, ruff:E1')
  })

  it('lists static-analysis scan issues per file', async () => {
    vi.mocked(api.createSubmission).mockResolvedValue(
      submission({ scan_issues: { 'b.py': 'Static analysis not run: full file not available.' } }),
    )
    renderDashboard()
    await upload(/Upload Patch File/i)
    expect(
      await screen.findByText('Static analysis issue for b.py: Static analysis not run: full file not available.'),
    ).toBeInTheDocument()
  })

  it('renders nothing when the submission has no review', async () => {
    vi.mocked(api.createSubmission).mockResolvedValue(submission({ review: undefined }))
    renderDashboard()
    await upload(/Upload Patch File/i)
    await screen.findByText('x.patch')
    expect(screen.queryByText('Review Results')).not.toBeInTheDocument()
  })

  it('hides the download button when there is no report', async () => {
    vi.mocked(api.createSubmission).mockResolvedValue(submission({ report: undefined }))
    renderDashboard()
    await upload(/Upload Patch File/i)
    await screen.findByText('Review Results')
    expect(screen.queryByRole('button', { name: 'Download Report' })).not.toBeInTheDocument()
  })

  it('names the downloaded report after the file minus its extension', async () => {
    vi.mocked(api.createSubmission).mockResolvedValue(submission())
    const createUrl = vi.fn(() => 'blob:fake')
    const revokeUrl = vi.fn()
    Object.assign(URL, { createObjectURL: createUrl, revokeObjectURL: revokeUrl })
    const clicked: HTMLAnchorElement[] = []
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      clicked.push(this)
    })

    renderDashboard()
    await upload(/Upload Patch File/i)
    await userEvent.setup().click(await screen.findByRole('button', { name: 'Download Report' }))

    expect(clicked[0].download).toBe('my.review-report.txt')
    expect(createUrl).toHaveBeenCalledTimes(1)
    expect(revokeUrl).toHaveBeenCalledWith('blob:fake')
  })
})

describe('Dashboard upload edge cases', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.resetAllMocks()
    vi.mocked(api.getGithubStatus).mockResolvedValue({ connected: false })
  })

  it('the Git Diff tab submits with type "diff"', async () => {
    vi.mocked(api.createSubmission).mockResolvedValue(submission({ submission_type: 'diff' }))
    renderDashboard()
    await userEvent.setup().click(screen.getByRole('tab', { name: /Upload Git Diff/ }))
    await upload(/Upload Git Diff File/i, 'x.diff')
    await waitFor(() =>
      expect(api.createSubmission).toHaveBeenCalledWith('diff', expect.any(File), 'test-token'),
    )
  })

  it('shows "Uploading..." and disables the button while the request is in flight', async () => {
    let resolve!: (s: Submission) => void
    vi.mocked(api.createSubmission).mockReturnValue(new Promise<Submission>((r) => (resolve = r)))
    renderDashboard()
    await upload(/Upload Patch File/i)

    const busy = await screen.findByRole('button', { name: 'Uploading...' })
    expect(busy).toBeDisabled()
    resolve(submission())
    expect(await screen.findByRole('button', { name: 'Upload Patch File' })).toBeEnabled()
  })

  it('uses a generic message when the rejection is not an Error', async () => {
    vi.mocked(api.createSubmission).mockRejectedValue('weird')
    renderDashboard()
    await upload(/Upload Patch File/i)
    expect(await screen.findByText('Upload failed: Request failed.')).toBeInTheDocument()
  })

  it('clears a previous error when the next upload succeeds', async () => {
    vi.mocked(api.createSubmission)
      .mockRejectedValueOnce(new Error('first failure'))
      .mockResolvedValueOnce(submission())
    renderDashboard()
    await upload(/Upload Patch File/i)
    expect(await screen.findByText('Upload failed: first failure')).toBeInTheDocument()
    await upload(/Upload Patch File/i)
    await screen.findByText('x.patch')
    expect(screen.queryByText(/first failure/)).not.toBeInTheDocument()
  })

  it('does nothing when the file dialog is cancelled', async () => {
    renderDashboard()
    const input = document.querySelector('input[type="file"]') as HTMLInputElement
    input.dispatchEvent(new Event('change', { bubbles: true }))
    expect(api.createSubmission).not.toHaveBeenCalled()
  })

  it('accepts a file exactly at the 10 MB limit', async () => {
    vi.mocked(api.createSubmission).mockResolvedValue(submission())
    renderDashboard()
    const user = userEvent.setup()
    const button = screen.getAllByRole('button', { name: /Upload Patch File/i })[0]
    const input = button.parentElement!.querySelector('input[type="file"]') as HTMLInputElement
    await user.upload(input, new File([new Uint8Array(10 * 1024 * 1024)], 'edge.patch'))
    await waitFor(() => expect(api.createSubmission).toHaveBeenCalled())
  })

  it('clicking the visible button opens the hidden file input', async () => {
    renderDashboard()
    const input = document.querySelector('input[type="file"]') as HTMLInputElement
    const click = vi.spyOn(input, 'click')
    await userEvent.setup().click(screen.getByRole('button', { name: 'Upload Patch File' }))
    expect(click).toHaveBeenCalled()
  })

  it('shows the signed-in username and logs out', async () => {
    renderDashboard()
    expect(screen.getByText('alice', { selector: '.username-badge' })).toBeInTheDocument()
    await userEvent.setup().click(screen.getByRole('button', { name: 'Log Out' }))
    expect(localStorage.getItem('token')).toBeNull()
    expect(screen.queryByText(/Welcome back/)).not.toBeInTheDocument()
  })

  it('tab switching marks the active tab', async () => {
    renderDashboard()
    const user = userEvent.setup()
    expect(screen.getByRole('tab', { name: /Upload Patch/ })).toHaveAttribute('aria-selected', 'true')
    await user.click(screen.getByRole('tab', { name: /Connect GitHub/ }))
    expect(screen.getByRole('tab', { name: /Connect GitHub/ })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tab', { name: /Upload Patch/ })).toHaveAttribute('aria-selected', 'false')
  })
})

describe('Dashboard GitHub panel edge cases', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.resetAllMocks()
  })

  async function openGithub() {
    renderDashboard()
    await userEvent.setup().click(screen.getByRole('tab', { name: /Connect GitHub/ }))
  }

  it('shows a loading placeholder (not a blank panel) while the status check is in flight', async () => {
    let resolve!: (v: { connected: boolean }) => void
    vi.mocked(api.getGithubStatus).mockReturnValue(new Promise((r) => (resolve = r)))
    await openGithub()

    expect(screen.getByRole('status')).toHaveTextContent('Checking GitHub connection')
    resolve({ connected: false })
    expect(await screen.findByLabelText(/GitHub Personal Access Token/)).toBeInTheDocument()
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('does not re-check GitHub when you switch away from the tab and come back', async () => {
    vi.mocked(api.getGithubStatus).mockResolvedValue({ connected: false })
    await openGithub()
    await screen.findByLabelText(/GitHub Personal Access Token/)

    const user = userEvent.setup()
    await user.click(screen.getByRole('tab', { name: /Upload Patch/ }))
    expect(screen.getByLabelText(/GitHub Personal Access Token/)).not.toBeVisible() // kept mounted, but hidden
    await user.click(screen.getByRole('tab', { name: /Connect GitHub/ }))

    expect(await screen.findByLabelText(/GitHub Personal Access Token/)).toBeInTheDocument()
    expect(api.getGithubStatus).toHaveBeenCalledTimes(1)
  })

  it('opts the token field out of password-manager autofill', async () => {
    vi.mocked(api.getGithubStatus).mockResolvedValue({ connected: false })
    await openGithub()
    const input = await screen.findByLabelText(/GitHub Personal Access Token/)
    expect(input).toHaveAttribute('autocomplete', 'off')
    expect(input).toHaveAttribute('data-1p-ignore')
  })

  it('treats a failed status check as "not connected"', async () => {
    vi.mocked(api.getGithubStatus).mockRejectedValue(new Error('offline'))
    await openGithub()
    expect(await screen.findByLabelText(/GitHub Personal Access Token/)).toBeInTheDocument()
  })

  it('keeps Connect disabled until a token is typed and trims whitespace', async () => {
    vi.mocked(api.getGithubStatus).mockResolvedValue({ connected: false })
    vi.mocked(api.connectGithub).mockResolvedValue({ connected: true })
    vi.mocked(api.listGithubRepos).mockResolvedValue([])
    await openGithub()
    const connect = await screen.findByRole('button', { name: 'Connect GitHub' })
    expect(connect).toBeDisabled()

    const user = userEvent.setup()
    await user.type(screen.getByLabelText(/GitHub Personal Access Token/), '   ')
    expect(connect).toBeDisabled()
    await user.type(screen.getByLabelText(/GitHub Personal Access Token/), ' ghp_abc ')
    await user.click(connect)
    expect(api.connectGithub).toHaveBeenCalledWith('test-token', 'ghp_abc')
  })

  it('uses a generic message when connecting fails with a non-Error', async () => {
    vi.mocked(api.getGithubStatus).mockResolvedValue({ connected: false })
    vi.mocked(api.connectGithub).mockRejectedValue('x')
    await openGithub()
    const user = userEvent.setup()
    await user.type(await screen.findByLabelText(/GitHub Personal Access Token/), 'ghp_abc')
    await user.click(screen.getByRole('button', { name: 'Connect GitHub' }))
    expect(await screen.findByText('Failed to connect GitHub.')).toBeInTheDocument()
  })

  it('reports repo-list failures', async () => {
    vi.mocked(api.getGithubStatus).mockResolvedValue({ connected: true })
    vi.mocked(api.listGithubRepos).mockRejectedValue(new Error('rate limited'))
    await openGithub()
    expect(await screen.findByText('rate limited')).toBeInTheDocument()
  })

  it('uses generic messages for non-Error repo-list failures', async () => {
    vi.mocked(api.getGithubStatus).mockResolvedValue({ connected: true })
    vi.mocked(api.listGithubRepos).mockRejectedValue('x')
    await openGithub()
    expect(await screen.findByText('Failed to load repos.')).toBeInTheDocument()
  })

  describe('with a connected account', () => {
    const pull = { id: 11, number: 7, title: 'Fix bug', html_url: 'https://github.com/o/r/pull/7' }

    beforeEach(() => {
      vi.mocked(api.getGithubStatus).mockResolvedValue({ connected: true })
      vi.mocked(api.listGithubRepos).mockResolvedValue([{ id: 1, full_name: 'o/r' }])
      vi.mocked(api.listGithubPulls).mockResolvedValue([pull])
    })

    it('hides the PR picker until a repo is chosen and resets it when the repo is cleared', async () => {
      await openGithub()
      const user = userEvent.setup()
      const repoSelect = await screen.findByLabelText('Repository')
      await screen.findByRole('option', { name: 'o/r' })
      expect(screen.queryByLabelText('Pull Request')).not.toBeInTheDocument()

      await user.selectOptions(repoSelect, 'o/r')
      expect(await screen.findByLabelText('Pull Request')).toBeInTheDocument()
      await user.selectOptions(repoSelect, '')
      expect(screen.queryByLabelText('Pull Request')).not.toBeInTheDocument()
      expect(api.listGithubPulls).toHaveBeenCalledTimes(1)
    })

    it('reports pull-request list failures (Error and non-Error)', async () => {
      vi.mocked(api.listGithubPulls).mockRejectedValueOnce(new Error('nope')).mockRejectedValueOnce('x')
      await openGithub()
      const user = userEvent.setup()
      const repoSelect = await screen.findByLabelText('Repository')
      await screen.findByRole('option', { name: 'o/r' })
      await user.selectOptions(repoSelect, 'o/r')
      expect(await screen.findByText('nope')).toBeInTheDocument()
      await user.selectOptions(repoSelect, '')
      await user.selectOptions(repoSelect, 'o/r')
      expect(await screen.findByText('Failed to load pull requests.')).toBeInTheDocument()
    })

    async function pickPull() {
      await openGithub()
      const user = userEvent.setup()
      const repoSelect = await screen.findByLabelText('Repository')
      await screen.findByRole('option', { name: 'o/r' })
      await user.selectOptions(repoSelect, 'o/r')
      await screen.findByRole('option', { name: '#7 Fix bug' })
      await user.selectOptions(screen.getByLabelText('Pull Request'), '7')
      return user
    }

    it('shows submit failures, with a generic fallback for non-Errors', async () => {
      vi.mocked(api.submitPullRequest).mockRejectedValueOnce(new Error('GitHub is not connected.')).mockRejectedValueOnce('x')
      const user = await pickPull()
      await user.click(screen.getByRole('button', { name: 'Submit for Review' }))
      expect(await screen.findByText('GitHub is not connected.')).toBeInTheDocument()
      await user.click(screen.getByRole('button', { name: 'Submit for Review' }))
      expect(await screen.findByText('Failed to submit pull request.')).toBeInTheDocument()
    })

    it('"Use a different token" returns to the token form and clears errors', async () => {
      vi.mocked(api.submitPullRequest).mockRejectedValue(new Error('bad'))
      const user = await pickPull()
      await user.click(screen.getByRole('button', { name: 'Submit for Review' }))
      await screen.findByText('bad')

      await user.click(screen.getByRole('button', { name: 'Use a different token' }))
      const form = screen.getByLabelText(/GitHub Personal Access Token/).closest('.github-section') as HTMLElement
      expect(within(form).getByLabelText(/GitHub Personal Access Token/)).toHaveValue('')
      expect(screen.queryByText('bad')).not.toBeInTheDocument()
    })
  })
})
