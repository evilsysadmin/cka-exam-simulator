import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath } from 'node:url'

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: 'dist-public',
    emptyOutDir: true,
    rollupOptions: { input: fileURLToPath(new URL('./public-preview.html', import.meta.url)) },
  },
})
