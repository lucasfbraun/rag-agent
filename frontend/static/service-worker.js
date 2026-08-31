// Service Worker mínimo do PU Matcher.
//
// Precisa ser servido em "/" (raiz), não em "/app/static/", porque o escopo
// padrão de um Service Worker é o diretório onde o próprio arquivo está —
// e o Chrome só considera o app instalável se o SW controla o start_url do
// manifest (aqui, "/"). O Streamlit não tem como servir estático na raiz,
// por isso o proxy Caddy serve este arquivo diretamente (ver ../../proxy/Caddyfile).
//
// Não cacheamos as páginas do Streamlit em si: é um app com WebSocket vivo
// (_stcore/stream), e um cache-first genérico quebraria a sessão. Só o
// "app shell" estático (manifest + ícone) é cacheado; o resto passa direto
// pra rede. O listener de "fetch" existe porque o Chrome exige um Service
// Worker com handler de fetch pra considerar o app instalável — sem isso o
// evento beforeinstallprompt nunca dispara.
const CACHE_NAME = "pu-matcher-shell-v1";
const APP_SHELL = ["/app/static/manifest.json", "/app/static/icon.svg"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_SHELL))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))
        )
      )
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const isAppShellAsset = APP_SHELL.some((path) => event.request.url.endsWith(path));
  if (!isAppShellAsset) {
    return; // deixa a rede/o browser tratar normalmente (inclusive WebSocket)
  }
  event.respondWith(
    caches.match(event.request).then((cached) => cached || fetch(event.request))
  );
});
