import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 3000,
    proxy: {
      '/api': 'http://127.0.0.1:8000',
      '/metrics': 'http://127.0.0.1:8000',
      '/events': 'http://127.0.0.1:8000',
      '/healthz': 'http://127.0.0.1:8000',
      // /disputes is both a SPA route AND an API path.
      // The bypass function checks Accept: text/html to distinguish
      // browser navigation (SPA) from API calls (AJAX/fetch).
      // Browser navigations get rewritten to /index.html so Vite
      // serves the SPA; API requests get proxied to the backend.
      '/disputes': {
        target: 'http://127.0.0.1:8000',
        bypass: (req) => {
          const accept = req.headers.accept || ''
          if (accept.includes('text/html')) {
            // Browser navigation — serve the SPA shell
            return '/index.html'
          }
          // API call — proxy to backend
          return undefined
        },
      },
    },
  },
})
