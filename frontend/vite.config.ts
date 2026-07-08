import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  // Relative base so built assets resolve under file:// inside the Electron app.
  base: './',
  plugins: [react()],
})
