/**
 * Custom Next.js server that:
 * 1. Proxies WebSocket upgrades for /teleradio/api/ws/* → backend :8000/api/ws/*
 *    so the WS works both when accessed directly (:8502) and via nginx reverse
 *    proxy (dihana.unizar.es) without needing port 8000 reachable externally.
 * 2. Rejects /_stcore/* WebSocket upgrades (stale Streamlit browser sessions).
 */
const { createServer } = require('http');
const { parse } = require('url');
const next = require('next');
const httpProxy = require('http-proxy');

const dev  = process.env.NODE_ENV !== 'production';
const port = parseInt(process.env.PORT || '8502', 10);
const app  = next({ dev });
const handle = app.getRequestHandler();

// Backend target for WS proxy (always local — server-to-server)
const BACKEND_WS = process.env.BACKEND_WS_URL || 'http://localhost:8000';
const BASE_PATH  = process.env.NEXT_PUBLIC_BASE_PATH || '/teleradio';

app.prepare().then(() => {
  // Suppress the "Error handling upgrade request" log that Next.js 14 emits
  // when its internal WS router receives a path it cannot handle.
  const _origError = console.error;
  console.error = (...args) => {
    if (typeof args[0] === 'string' && args[0].includes('Error handling upgrade request')) return;
    _origError.apply(console, args);
  };

  // Create a proxy server for WebSocket connections to the backend
  const proxy = httpProxy.createProxyServer({ target: BACKEND_WS, ws: true });
  proxy.on('error', (err, req, socket) => {
    console.error('[WS proxy error]', err.message);
    if (socket && socket.writable) {
      socket.write('HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n');
      socket.destroy();
    }
  });

  const server = createServer((req, res) => {
    const parsedUrl = parse(req.url, true);
    handle(req, res, parsedUrl);
  });

  // prependListener runs BEFORE any Next.js internal upgrade handler
  server.prependListener('upgrade', (req, socket, head) => {
    const url = req.url || '';

    // ── 1. Proxy backend WS through Next.js server ──────────────────────
    // Browser connects to ws://<host>[:<port>]/teleradio/ws/chat
    // We strip the basePath prefix and forward to ws://localhost:8000/ws/chat
    if (url.startsWith(`${BASE_PATH}/ws/`)) {
      const targetPath = url.slice(BASE_PATH.length); // /ws/chat
      req.url = targetPath;
      proxy.ws(req, socket, head);
      return;
    }

    // ── 2. Reject stale Streamlit WS requests ───────────────────────────
    if (url.includes('_stcore') || url.includes('/stream')) {
      socket.on('error', () => {});
      socket.write('HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\nConnection: close\r\n\r\n');
      socket.destroy();
      return;
    }

    // ── 3. Let Next.js handle everything else (HMR, etc.) ───────────────
  });

  server.listen(port, '0.0.0.0', () => {
    console.log(`> Ready on http://0.0.0.0:${port}`);
    console.log(`> WS proxy: ${BASE_PATH}/ws/* → ${BACKEND_WS}/ws/*`);
  });
});
