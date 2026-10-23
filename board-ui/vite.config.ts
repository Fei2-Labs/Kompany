import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';
import { VitePWA } from 'vite-plugin-pwa';

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
    plugins: [
      react(),
      VitePWA({
        // Registered by hand in src/pwa.ts so Tauri can be skipped.
        injectRegister: false,
        registerType: 'autoUpdate',
        manifest: {
          name: 'Kompany',
          short_name: 'Kompany',
          description: 'Kompany control surface',
          // HashRouter + served at the FastAPI root, so `/` is the whole app.
          start_url: '/',
          scope: '/',
          display: 'standalone',
          orientation: 'portrait',
          theme_color: '#0c0d10',
          background_color: '#0c0d10',
          icons: [
            { src: '/icon-192.png', sizes: '192x192', type: 'image/png' },
            { src: '/icon-512.png', sizes: '512x512', type: 'image/png' },
            { src: '/icon-maskable-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
          ],
        },
        workbox: {
          // Precache the app shell only. No runtimeCaching: API calls, writes
          // and the `/events` SSE stream always go to the network.
          globPatterns: ['**/*.{js,css,html,png,webmanifest}'],
          navigateFallback: '/index.html',
          navigateFallbackDenylist: [/^\/(events|dashboard|ui|world|health|docs|redoc|openapi\.json|inbox|projects|agents|observability|status|llm|runtime|channel|directive|approvals|heartbeat|episodes|remote|intake)(\/|$)/],
          cleanupOutdatedCaches: true,
        },
      }),
    ],
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
