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

  server.listen(port, '0.0.0.0', () => {
    // Capture whatever upgrade listeners Next.js has registered (HMR etc.)
    // Replace them all with a single handler that filters _stcore first.
    const existingListeners = [...server.listeners('upgrade')];
    server.removeAllListeners('upgrade');

    server.on('upgrade', (req, socket, head) => {
      const url = req.url || '';
      if (url.startsWith('/_stcore') || url.startsWith('/teleradio/_stcore')) {
        // Close cleanly — stops Streamlit from retrying, avoids Next.js crash
        socket.write(
          'HTTP/1.1 400 Bad Request\r\n' +
          'Content-Length: 0\r\n' +
          'Connection: close\r\n\r\n'
        );
        socket.destroy();
        return;
      }
      // Forward to Next.js's own upgrade handlers (HMR websocket in dev)
      for (const fn of existingListeners) {
        fn.call(server, req, socket, head);
      }
    });

    console.log(`> Ready on http://0.0.0.0:${port}`);
  });
});
