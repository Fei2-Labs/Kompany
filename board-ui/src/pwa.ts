// Service-worker registration for the installable web app. Guarded so it is a
// no-op in the Tauri WebView (desktop ships the shell itself) and on insecure
// origins, where browsers refuse service workers anyway.

export function shouldRegisterServiceWorker(env: {
  hasServiceWorker: boolean;
  isSecureContext: boolean;
  isTauri: boolean;
}): boolean {
  return env.hasServiceWorker && env.isSecureContext && !env.isTauri;
}

export function registerServiceWorker(): void {
  if (typeof window === 'undefined' || typeof navigator === 'undefined') return;
  const ok = shouldRegisterServiceWorker({
    hasServiceWorker: 'serviceWorker' in navigator,
    isSecureContext: window.isSecureContext,
    isTauri: '__TAURI_INTERNALS__' in window || '__TAURI__' in window || window.location.protocol === 'tauri:',
  });
  if (!ok) return;
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch(() => {
      // Offline shell is an enhancement; a failed registration must not surface.
    });
  });
}
