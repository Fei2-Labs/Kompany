import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

const DEFAULT_CORE_ORIGIN = 'http://127.0.0.1:8000';

// API routes the board talks to in dev. The Python engine owns these;
// Vite proxies them to the running sidecar so `npm run dev` works against
// a live engine without CORS. Mirrors kompany-world/vite.config.ts.
const API_ROUTES = [
  '/projects',
  '/inbox',
  '/episodes',
  '/agents/status',
  '/agents',
  '/events',
  '/status',
  '/observability',
  '/llm',
  '/runtime',
  '/channel',
  '/directive',
  '/approvals',
  '/heartbeat',
];

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const coreOrigin = env.KOMPANY_CORE_ORIGIN || DEFAULT_CORE_ORIGIN;

  const proxy = Object.fromEntries(
    API_ROUTES.map((route) => [
      route,
      { target: coreOrigin, changeOrigin: true },
    ]),
  );

  return {
    // Mounted at the FastAPI root (`/`); assets resolve from `/assets/*`.
    base: '/',
    plugins: [react()],
    server: {
      proxy,
    },
    build: {
      // Emit straight into the Python package so the wheel / PyInstaller
      // `--collect-all kompany` bundle ships the built board.
      outDir: '../kompany/src/kompany/board_ui/dist',
      emptyOutDir: true,
    },
  };
});
