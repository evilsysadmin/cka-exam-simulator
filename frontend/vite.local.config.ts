import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
export default defineConfig({plugins:[react()],build:{outDir:'dist-local',rollupOptions:{input:'local.html'}},define:{'import.meta.env.VITE_BUILD_SHA':JSON.stringify(process.env.VITE_BUILD_SHA || '0'.repeat(40))}})
