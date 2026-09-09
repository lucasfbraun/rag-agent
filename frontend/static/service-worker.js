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
// A versão faz parte do nome de propósito: `activate` apaga todo cache cujo
// nome seja diferente deste, então trocar o conteúdo de APP_SHELL sem trocar a
// versão deixaria as entradas antigas vivas para sempre. Bumpar ao mexer na
// lista abaixo. (v2: os ícones da marca substituíram o icon.svg placeholder.)
const CACHE_NAME = "pu-matcher-shell-v2";
const APP_SHELL = ["/app/static/manifest.json", "/app/static/icon-192.png", "/app/static/icon-512.png"];

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

// Rede primeiro, cache só como reserva offline.
//
// A versão anterior fazia o contrário (cache-first) e isso é uma armadilha
// com nome de cache fixo: quando os ícones da marca foram trocados em
// 2026-09-09, a URL continuou a mesma e o CACHE_NAME continuou "v1", então um
// navegador que já tinha visitado o app serviria o ícone ANTIGO para sempre —
// sem erro, sem sintoma, e impossível de resolver com F5.
//
// Cache-first só se justifica quando o ganho de latência importa. Aqui o
// "shell" são três arquivos pequenos que o navegador já cacheia por HTTP; o
// Service Worker existe por um motivo só, que é ter um handler de "fetch"
// para o Chrome considerar o app instalável. Rede primeiro entrega isso sem
// nenhum risco de servir asset velho, e o cache continua cobrindo o offline.
self.addEventListener("fetch", (event) => {
  const isAppShellAsset = APP_SHELL.some((path) => event.request.url.endsWith(path));
  if (!isAppShellAsset) {
    return; // deixa a rede/o browser tratar normalmente (inclusive WebSocket)
  }
  event.respondWith(
    fetch(event.request)
      .then((resposta) => {
        const copia = resposta.clone();
        caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copia));
        return resposta;
      })
      .catch(() => caches.match(event.request))
  );
});
