/**
 * Custom Next.js server that intercepts WebSocket upgrade requests
 * from stale Streamlit browser sessions before they reach Next.js
 * (Next.js 14 crashes with "Cannot read properties of undefined (reading 'bind')"
 * when it receives a WS upgrade it cannot handle).
 */
const { createServer } = require('http');
const { parse } = require('url');
const next = require('next');

const dev  = process.env.NODE_ENV !== 'production';
const port = parseInt(process.env.PORT || '8502', 10);
const app  = next({ dev });
const handle = app.getRequestHandler();

app.prepare().then(() => {
  const server = createServer((req, res) => {
    const parsedUrl = parse(req.url, true);
    handle(req, res, parsedUrl);
  });

  // Use Next.js's own upgrade handler (handles HMR websocket in dev).
  // We wrap it so we can intercept and drop Streamlit /_stcore/* upgrades
  // before they reach Next.js (which crashes on unknown WS paths).
  const nextUpgradeHandler =
    typeof app.getUpgradeHandler === 'function'
      ? app.getUpgradeHandler()
      : null;

  server.on('upgrade', (req, socket, head) => {
    const url = req.url || '';
    if (url.includes('_stcore')) {
      socket.write(
        'HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\nConnection: close\r\n\r\n'
      );
      socket.destroy();
      return;
    }
    if (nextUpgradeHandler) {
      nextUpgradeHandler(req, socket, head);
    }
  });

  server.listen(port, '0.0.0.0', () => {
    console.log(`> Ready on http://0.0.0.0:${port}`);
  });
});
