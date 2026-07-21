import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './styles/fonts.css'
import './theme.css'
import './styles/base.css'
import App from './App.tsx'
import InstallHint from './InstallHint.tsx'
import { initTheme } from './theme.ts'

initTheme()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
    <InstallHint />
  </StrictMode>,
)
