import { defineConfig, mergeConfig } from 'vite'
import preview from './vite.public.config'
export default mergeConfig(preview, defineConfig({
  define: { 'import.meta.env.VITE_SHADOWOPS_IDENTITY': JSON.stringify('central-staging') },
  build: { outDir: 'dist-account-staging' },
}))
