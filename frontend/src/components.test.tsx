import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import { AuthProvider } from './AuthContext'
import AuthBrandPanel from './AuthBrandPanel'
import BrandIcon from './BrandIcon'
import PasswordField from './PasswordField'
import ProtectedRoute from './ProtectedRoute'
import { ThemeProvider, useTheme } from './ThemeContext'
import ThemeToggle from './ThemeToggle'
import * as api from './api'

vi.mock('./api')

function renderApp(path: string) {
  return render(
    <ThemeProvider>
      <MemoryRouter initialEntries={[path]}>
        <AuthProvider>
          <App />
        </AuthProvider>
      </MemoryRouter>
    </ThemeProvider>,
  )
}

beforeEach(() => {
  localStorage.clear()
  document.documentElement.removeAttribute('data-theme')
  vi.resetAllMocks()
  vi.mocked(api.getGithubStatus).mockResolvedValue({ connected: false })
})

describe('PasswordField', () => {
  function Harness() {
    return (
      <PasswordField label="Password" value="hunter22" onChange={() => {}} required minLength={8} />
    )
  }

  it('hides the password by default and toggles visibility', async () => {
    const user = userEvent.setup()
    render(<Harness />)
    const input = screen.getByLabelText(/Password/, { selector: 'input' })
    expect(input).toHaveAttribute('type', 'password')

    await user.click(screen.getByRole('button', { name: 'Show password' }))
    expect(input).toHaveAttribute('type', 'text')

    await user.click(screen.getByRole('button', { name: 'Hide password' }))
    expect(input).toHaveAttribute('type', 'password')
  })

  it('passes required/minLength through and reports typed values', async () => {
    const onChange = vi.fn()
    const user = userEvent.setup()
    render(<PasswordField label="Password" value="" onChange={onChange} required minLength={8} />)
    const input = screen.getByLabelText(/Password/, { selector: 'input' })
    expect(input).toBeRequired()
    expect(input).toHaveAttribute('minlength', '8')
    await user.type(input, 'a')
    expect(onChange).toHaveBeenCalledWith('a')
  })
})

describe('Theme', () => {
  function ThemeProbe() {
    const { theme } = useTheme()
    return <span data-testid="theme">{theme}</span>
  }

  it('defaults to light when no preference is stored or set by the OS', () => {
    render(
      <ThemeProvider>
        <ThemeProbe />
      </ThemeProvider>,
    )
    expect(screen.getByTestId('theme')).toHaveTextContent('light')
    expect(document.documentElement).toHaveAttribute('data-theme', 'light')
  })

  it('follows the OS dark preference when nothing is stored', () => {
    vi.spyOn(window, 'matchMedia').mockReturnValue({ matches: true } as MediaQueryList)
    render(
      <ThemeProvider>
        <ThemeProbe />
      </ThemeProvider>,
    )
    expect(screen.getByTestId('theme')).toHaveTextContent('dark')
  })

  it('a stored theme wins over the OS preference', () => {
    localStorage.setItem('theme', 'light')
    vi.spyOn(window, 'matchMedia').mockReturnValue({ matches: true } as MediaQueryList)
    render(
      <ThemeProvider>
        <ThemeProbe />
      </ThemeProvider>,
    )
    expect(screen.getByTestId('theme')).toHaveTextContent('light')
  })

  it('ignores an invalid stored value', () => {
    localStorage.setItem('theme', 'purple')
    render(
      <ThemeProvider>
        <ThemeProbe />
      </ThemeProvider>,
    )
    expect(screen.getByTestId('theme')).toHaveTextContent('light')
  })

  it('toggle switches theme, updates the DOM attribute and persists it', async () => {
    const user = userEvent.setup()
    render(
      <ThemeProvider>
        <ThemeToggle />
        <ThemeProbe />
      </ThemeProvider>,
    )
    await user.click(screen.getByRole('button', { name: 'Switch to dark mode' }))
    expect(screen.getByTestId('theme')).toHaveTextContent('dark')
    expect(document.documentElement).toHaveAttribute('data-theme', 'dark')
    expect(localStorage.getItem('theme')).toBe('dark')

    await user.click(screen.getByRole('button', { name: 'Switch to light mode' }))
    expect(screen.getByTestId('theme')).toHaveTextContent('light')
  })

  it('useTheme throws outside a ThemeProvider', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
    expect(() => render(<ThemeProbe />)).toThrow('useTheme must be used within a ThemeProvider')
    spy.mockRestore()
  })
})

describe('Branding', () => {
  it('renders the brand panel with a decorative icon', () => {
    const { container } = render(<AuthBrandPanel />)
    expect(screen.getByRole('heading', { name: 'AI Code Review Platform' })).toBeInTheDocument()
    expect(container.querySelector('.auth-brand-icon')).toHaveAttribute('aria-hidden', 'true')
  })

  it('renders the icon svg', () => {
    const { container } = render(<BrandIcon />)
    expect(container.querySelector('svg')).toBeInTheDocument()
  })
})

describe('ProtectedRoute', () => {
  function renderProtected() {
    return render(
      <MemoryRouter initialEntries={['/secret']}>
        <AuthProvider>
          <Routes>
            <Route path="/login" element={<p>login page</p>} />
            <Route
              path="/secret"
              element={
                <ProtectedRoute>
                  <p>secret content</p>
                </ProtectedRoute>
              }
            />
          </Routes>
        </AuthProvider>
      </MemoryRouter>,
    )
  }

  it('redirects to /login without a token', () => {
    renderProtected()
    expect(screen.getByText('login page')).toBeInTheDocument()
    expect(screen.queryByText('secret content')).not.toBeInTheDocument()
  })

  it('renders children when a token exists', () => {
    localStorage.setItem('token', 't')
    renderProtected()
    expect(screen.getByText('secret content')).toBeInTheDocument()
  })
})

describe('Login page', () => {
  it('logs in, stores the session and lands on the dashboard', async () => {
    vi.mocked(api.login).mockResolvedValue({ token: 'tok', user: { id: '1', username: 'alice' } })
    const user = userEvent.setup()
    renderApp('/login')

    await user.type(screen.getByLabelText('Username'), 'alice')
    await user.type(screen.getByLabelText(/Password/, { selector: 'input' }), 'password123')
    await user.click(screen.getByRole('button', { name: 'Log In' }))

    expect(api.login).toHaveBeenCalledWith('alice', 'password123')
    expect(await screen.findByText('Welcome back, alice')).toBeInTheDocument()
    expect(localStorage.getItem('token')).toBe('tok')
  })

  it('shows the server error and stays on the page when login fails', async () => {
    vi.mocked(api.login).mockRejectedValue(new Error('Invalid username or password.'))
    const user = userEvent.setup()
    renderApp('/login')

    await user.type(screen.getByLabelText('Username'), 'alice')
    await user.type(screen.getByLabelText(/Password/, { selector: 'input' }), 'wrongpass1')
    await user.click(screen.getByRole('button', { name: 'Log In' }))

    expect(await screen.findByText('Invalid username or password.')).toBeInTheDocument()
    expect(localStorage.getItem('token')).toBeNull()
  })

  it('falls back to a generic message for non-Error rejections and clears it on retry', async () => {
    vi.mocked(api.login).mockRejectedValueOnce('boom').mockResolvedValueOnce({
      token: 't',
      user: { id: '1', username: 'alice' },
    })
    const user = userEvent.setup()
    renderApp('/login')

    await user.type(screen.getByLabelText('Username'), 'alice')
    await user.type(screen.getByLabelText(/Password/, { selector: 'input' }), 'password123')
    await user.click(screen.getByRole('button', { name: 'Log In' }))
    expect(await screen.findByText('Login failed.')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Log In' }))
    expect(await screen.findByText('Welcome back, alice')).toBeInTheDocument()
  })

  it('links to sign up', async () => {
    const user = userEvent.setup()
    renderApp('/login')
    await user.click(screen.getByRole('link', { name: 'Sign up' }))
    expect(screen.getByRole('heading', { name: 'Sign Up' })).toBeInTheDocument()
  })
})

describe('Signup page', () => {
  it('signs up and lands on the dashboard', async () => {
    vi.mocked(api.signup).mockResolvedValue({ token: 'tok', user: { id: '2', username: 'bob' } })
    const user = userEvent.setup()
    renderApp('/signup')

    await user.type(screen.getByLabelText('Username'), 'bob')
    await user.type(screen.getByLabelText(/Password/, { selector: 'input' }), 'password123')
    await user.click(screen.getByRole('button', { name: 'Sign Up' }))

    expect(api.signup).toHaveBeenCalledWith('bob', 'password123')
    expect(await screen.findByText('Welcome back, bob')).toBeInTheDocument()
  })

  it('enforces an 8 character minimum on the password field', () => {
    renderApp('/signup')
    expect(screen.getByLabelText(/Password/, { selector: 'input' })).toHaveAttribute('minlength', '8')
  })

  it('shows server errors (e.g. duplicate username)', async () => {
    vi.mocked(api.signup).mockRejectedValue(new Error('An account with this username already exists.'))
    const user = userEvent.setup()
    renderApp('/signup')

    await user.type(screen.getByLabelText('Username'), 'bob')
    await user.type(screen.getByLabelText(/Password/, { selector: 'input' }), 'password123')
    await user.click(screen.getByRole('button', { name: 'Sign Up' }))

    expect(await screen.findByText('An account with this username already exists.')).toBeInTheDocument()
  })

  it('falls back to a generic message for non-Error rejections', async () => {
    vi.mocked(api.signup).mockRejectedValue('nope')
    const user = userEvent.setup()
    renderApp('/signup')

    await user.type(screen.getByLabelText('Username'), 'bob')
    await user.type(screen.getByLabelText(/Password/, { selector: 'input' }), 'password123')
    await user.click(screen.getByRole('button', { name: 'Sign Up' }))

    expect(await screen.findByText('Sign up failed.')).toBeInTheDocument()
  })

  it('links back to login', async () => {
    const user = userEvent.setup()
    renderApp('/signup')
    await user.click(screen.getByRole('link', { name: 'Log in' }))
    expect(screen.getByRole('heading', { name: 'Log In' })).toBeInTheDocument()
  })
})

describe('App routing', () => {
  it('sends unauthenticated users from / to the login page', () => {
    renderApp('/')
    expect(screen.getByRole('heading', { name: 'Log In' })).toBeInTheDocument()
  })

  it('sends unknown routes to / (and then to login when signed out)', () => {
    renderApp('/definitely/not/a/page')
    expect(screen.getByRole('heading', { name: 'Log In' })).toBeInTheDocument()
  })

  it('shows the dashboard for a signed-in user and logs out back to login', async () => {
    localStorage.setItem('token', 't')
    localStorage.setItem('user', JSON.stringify({ id: '1', username: 'alice' }))
    const user = userEvent.setup()
    renderApp('/')

    expect(screen.getByText('Welcome back, alice')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Log Out' }))
    expect(screen.getByRole('heading', { name: 'Log In' })).toBeInTheDocument()
    expect(localStorage.getItem('token')).toBeNull()
  })
})
