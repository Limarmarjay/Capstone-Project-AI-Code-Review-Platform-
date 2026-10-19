import BrandIcon from './BrandIcon'

function AuthBrandPanel() {
  return (
    <div className="auth-brand">
      <div className="auth-brand-icon" aria-hidden="true">
        <BrandIcon />
      </div>
      <h2>AI Code Review Platform</h2>
    </div>
  )
}

export default AuthBrandPanel
