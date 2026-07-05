import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 开发服务器:把 /api 的请求转发到后端 FastAPI(localhost:8000),
// 这样前端代码里直接写 fetch('/api/...') 即可,无需关心端口与跨域。
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8000',
    },
  },
})
