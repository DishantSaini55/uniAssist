import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const proxy = { '/api': { target: process.env.BACKEND_URL || 'http://localhost:8000', changeOrigin: true, rewrite: p => p.replace(/^\/api/, '') } };
export default defineConfig({
  plugins: [react()],
  server: { host: '0.0.0.0', port: 5173, proxy },
  preview: { host: '0.0.0.0', port: 5173, proxy },
});
