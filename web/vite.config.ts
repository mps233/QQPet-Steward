import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 迁移已完成：新版就是 `/`，产物放 /assets/ 下。
// '/next' 路由在 dashboard.py 里保留作兼容别名（老书签/主屏图标仍能用）。
export default defineConfig({
  plugins: [react()],
  base: '/',
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    // 生产构建：压缩 + 不产出 sourcemap。
    // 历史：迁移期为了调试写成 minify:false + sourcemap:true，注释也写着"迁完可去掉"，
    // 但迁移完成后一直没去掉。代价（实测）：JS 明文 470KB（gzip 后 109KB）、
    // 外加一个 804KB 的 .map 被 dashboard.py 的 /assets/* 白名单公开下载
    // （而仪表盘本身无鉴权）。见 docs/review/03-p0-交付链与安全.md A/D 节。
    // 需要调试时用 `npm run dev` —— Vite dev server 自带 sourcemap，与产物无关。
    minify: 'esbuild',
    sourcemap: false,
  },
  server: {
    port: 5173,
    // dev 时 API / 静态资源转发到本机 Python 仪表盘（8787）
    proxy: {
      '/api': 'http://127.0.0.1:8787',
      '/stream': 'http://127.0.0.1:8787',
      '/files': 'http://127.0.0.1:8787',
      '/qp-icons': 'http://127.0.0.1:8787',
      '/icon-192.png': 'http://127.0.0.1:8787',
      '/icon-512.png': 'http://127.0.0.1:8787',
      '/apple-touch-icon.png': 'http://127.0.0.1:8787',
    },
  },
})
