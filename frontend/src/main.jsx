import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'
import { initSecurityShield } from './utils/security.js'

// ── Security Shield ─────────────────────────────────────────────────
// Initialize before render so protection is active immediately
initSecurityShield();

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
