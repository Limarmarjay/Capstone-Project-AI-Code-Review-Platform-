import { useState } from 'react'
import type { SubmitEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { signup } from '../api'
import { useAuth } from '../AuthContext'
import '../App.css'

function Signup() {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const { setSession } = useAuth()
  const navigate = useNavigate()

  const handleSubmit = async (event: SubmitEvent) => {
    event.preventDefault()
    setError(null)
    try {
      const { token, user } = await signup(username, password)
      setSession(token, user)
      navigate('/')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Sign up failed.')
    }
  }

  return (
    <div className="auth-page">
      <form className="auth-form" onSubmit={handleSubmit}>
        <h1>Sign Up</h1>
        <label>
          Username
          <input value={username} onChange={(e) => setUsername(e.target.value)} required />
        </label>
        <label>
          Password
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            minLength={8}
          />
        </label>
        {error && <p className="auth-error">{error}</p>}
        <button type="submit">Sign Up</button>
        <p>
          Already have an account? <Link to="/login">Log in</Link>
        </p>
      </form>
    </div>
  )
}

export default Signup
