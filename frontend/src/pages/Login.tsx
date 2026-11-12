import { useState } from 'react'
import type { SubmitEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { login } from '../api'
import AuthBrandPanel from '../AuthBrandPanel'
import { useAuth } from '../AuthContext'
import PasswordField from '../PasswordField'
import ThemeToggle from '../ThemeToggle'
import '../App.css'

function Login() {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const { setSession } = useAuth()
  const navigate = useNavigate()

  const handleSubmit = async (event: SubmitEvent) => {
    event.preventDefault()
    setError(null)
    try {
      const { token, user } = await login(username, password)
      setSession(token, user)
      navigate('/')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed.')
    }
  }

  return (
    <div className="auth-page">
      <ThemeToggle />
      <div className="auth-layout">
        <AuthBrandPanel />
        <form className="auth-form" onSubmit={handleSubmit}>
          <h1>Log In</h1>
          <label>
            Username
            <input value={username} onChange={(e) => setUsername(e.target.value)} required />
          </label>
          <PasswordField label="Password" value={password} onChange={setPassword} required />
          {error && <p className="auth-error">{error}</p>}
          <button type="submit">Log In</button>
          <p>
            Don't have an account? <Link to="/signup">Sign up</Link>
          </p>
        </form>
      </div>
    </div>
  )
}

export default Login
