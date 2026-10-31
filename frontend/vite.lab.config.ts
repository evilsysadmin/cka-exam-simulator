import { defineConfig, mergeConfig } from 'vite'
import account from './vite.account.config'
export default mergeConfig(account, defineConfig({
  define: { 'import.meta.env.VITE_CKA_LABS_STAGING': JSON.stringify('pilot') },
  build: { outDir: 'dist-lab-staging' },
}))
