import { useState } from 'react'

interface PasswordFieldProps {
  label: string
  value: string
  onChange: (value: string) => void
  required?: boolean
  minLength?: number
}

function EyeIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path
        d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7-11-7-11-7z"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx="12" cy="12" r="3" stroke="currentColor" strokeWidth="2" />
    </svg>
  )
}

function EyeOffIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path
        d="M9.9 4.24A10.4 10.4 0 0112 4c7 0 11 7 11 7a18.5 18.5 0 01-3.2 4.1M6.6 6.6C3.2 8.6 1 12 1 12s4 7 11 7a10.4 10.4 0 005.1-1.3M9.9 14.1a3 3 0 104.2-4.2"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path d="M1 1l22 22" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  )
}

function PasswordField({ label, value, onChange, required, minLength }: PasswordFieldProps) {
  const [visible, setVisible] = useState(false)

  return (
    <label>
      {label}
      <div className="password-field">
        <input
          type={visible ? 'text' : 'password'}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          required={required}
          minLength={minLength}
        />
        <button
          type="button"
          className="password-toggle"
          onClick={() => setVisible((current) => !current)}
          aria-label={visible ? 'Hide password' : 'Show password'}
        >
          {visible ? <EyeIcon /> : <EyeOffIcon />}
        </button>
      </div>
    </label>
  )
}

export default PasswordField
