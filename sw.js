/* 耳辩 · Service Worker 已停用
 *
 * 之前这里做的是离线缓存（cache-first），公网部署后出现两个问题：
 *   1. 改了 HTML 刷新看不到变化，必须手动清缓存；
 *   2. 页面和资源版本错配，偶尔出现"点棋子没反应"。
 *
 * 现在改为自毁：浏览器一旦拿到这份文件，会立即接管、
 * 清空所有旧缓存、然后注销自己，之后再也不会拦截任何请求。
 */
self.addEventListener('install', () => {
  self.skipWaiting();
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.map(key => caches.delete(key))))
      .then(() => self.registration.unregister())
      .then(() => self.clients.claim())
      .catch(() => {})
  );
});
