import BrandIcon from './BrandIcon'

function AuthBrandPanel() {
  return (
    <div className="auth-brand">
      <div className="auth-brand-icon" aria-hidden="true">
        <BrandIcon />
      </div>
      <h2>AI Code Review Platform</h2>
      <p>Catch bugs and security issues in your Python changes before they ship.</p>
      <ul className="auth-features">
        <li>Static analysis and AI review, combined</li>
        <li>Secrets are redacted before anything is analyzed</li>
        <li>Works with patches, diffs and GitHub pull requests</li>
      </ul>
    </div>
  )
}

export default AuthBrandPanel
