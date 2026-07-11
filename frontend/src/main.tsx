import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './theme.css'
import './styles/base.css'
import App from './App.tsx'
import { initTheme } from './theme.ts'

initTheme()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
