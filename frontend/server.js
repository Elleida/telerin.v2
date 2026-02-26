/**
 * Custom Next.js server that intercepts WebSocket upgrade requests
 * from stale Streamlit browser sessions before they reach Next.js.
 *
 * Next.js 14 DevServer registers its upgrade handler internally (not via
 * server.on('upgrade')) and crashes with "Cannot read properties of undefined
 * (reading 'bind')" when it receives a WS path it cannot route.
 *
 * Strategy: use prependListener so our filter runs first; destroy the socket
 * for /_stcore/* paths and add an error handler so the subsequent Next.js
 * code fails silently on the already-destroyed socket.
 */
const { createServer } = require('http');
const { parse } = require('url');
const next = require('next');

const dev  = process.env.NODE_ENV !== 'production';
const port = parseInt(process.env.PORT || '8502', 10);
const app  = next({ dev });
const handle = app.getRequestHandler();

app.prepare().then(() => {
  // Suppress the "Error handling upgrade request" log that Next.js 14 emits
  // when its internal WS router receives a path it cannot handle (Streamlit).
  const _origError = console.error;
  console.error = (...args) => {
    if (typeof args[0] === 'string' && args[0].includes('Error handling upgrade request')) return;
    _origError.apply(console, args);
  };

  const server = createServer((req, res) => {
    const parsedUrl = parse(req.url, true);
    handle(req, res, parsedUrl);
  });

  // prependListener ensures we run BEFORE any listener Next.js may register
  server.prependListener('upgrade', (req, socket) => {
    const url = req.url || '';
    if (url.includes('_stcore') || url.includes('/stream')) {
      // Absorb any errors Next.js triggers on the destroyed socket
      socket.on('error', () => {});
      socket.write(
        'HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\nConnection: close\r\n\r\n'
      );
      socket.destroy();
    }
  });

  server.listen(port, '0.0.0.0', () => {
    console.log(`> Ready on http://0.0.0.0:${port}`);
  });
});
