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

  // Intercept ALL WebSocket upgrade requests.
  // Streamlit clients try to open ws://.../stream after health checks.
  // Next.js cannot handle these and throws an unhandled error — so we
  // destroy the socket here before Next.js sees it.
  server.on('upgrade', (req, socket) => {
    const url = req.url || '';
    if (url.startsWith('/_stcore') || url.startsWith('/teleradio/_stcore')) {
      // Close cleanly — this stops Streamlit from retrying indefinitely
      socket.write(
        'HTTP/1.1 400 Bad Request\r\n' +
        'Content-Length: 0\r\n' +
        'Connection: close\r\n\r\n'
      );
      socket.destroy();
      return;
    }
    // Let any other upgrades through (e.g. Next.js HMR in dev)
    // Nothing to do — Next.js registers its own 'upgrade' handler
  });

  server.listen(port, '0.0.0.0', () => {
    console.log(`> Ready on http://0.0.0.0:${port}`);
  });
});
