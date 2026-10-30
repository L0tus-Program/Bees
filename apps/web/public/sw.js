/* global self, caches */
// Service worker mínimo: instala o app e mantém a casca da interface offline.
// Nunca guarda respostas da API, sessões ou arquivos do usuário.
const CACHE = 'bees-shell-v1'
const SHELL = ['/', '/manifest.webmanifest', '/bee.svg', '/icon-192.png', '/icon-512.png']

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()))
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  )
})

self.addEventListener('fetch', (event) => {
  const request = event.request
  const url = new URL(request.url)
  if (request.method !== 'GET' || url.origin !== self.location.origin || url.pathname.startsWith('/api/')) return
  if (request.mode === 'navigate') {
    // Rede primeiro; offline mostra a última casca conhecida.
    event.respondWith(fetch(request).then((response) => {
      const copy = response.clone()
      if (response.ok) caches.open(CACHE).then((cache) => cache.put('/', copy))
      return response
    }).catch(() => caches.match('/')))
    return
  }
  if (url.pathname.startsWith('/assets/')) {
    // Assets do build têm hash no nome: podem vir do cache.
    event.respondWith(caches.match(request).then((cached) => cached || fetch(request).then((response) => {
      const copy = response.clone()
      if (response.ok) caches.open(CACHE).then((cache) => cache.put(request, copy))
      return response
    })))
  }
})
