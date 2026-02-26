/** @type {import('next').NextConfig} */
const nextConfig = {
  // Serve the app under /teleradio (same path as the previous Streamlit app)
  basePath: '/teleradio',
  // Increase proxy timeout for long-running LLM calls (default is ~30s)
  experimental: {
    proxyTimeout: 120_000,
  },
  async rewrites() {
    // Normalize possible ws:// or wss:// env value to http(s) so Next accepts it
    const wsBaseRaw = process.env.NEXT_PUBLIC_WS_URL || 'http://localhost:8000'
    const wsBase = wsBaseRaw.replace(/^ws:/i, 'http:').replace(/^wss:/i, 'https:')
    return {
      // beforeFiles runs before basePath is applied, so we can catch /_stcore/*
      // requests from stale Streamlit browser sessions and route them to our
      // handler at /teleradio/_stcore/* (where app router serves them).
      beforeFiles: [
        { source: '/_stcore/:path*', destination: '/teleradio/_stcore/:path*' },
      ],
      afterFiles: [
        {
          source: '/api/:path*',
          destination: `${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/:path*`,
        },
        {
          source: '/ws/:path*',
          destination: `${wsBase}/ws/:path*`,
        },
      ],
    };
  },
};

module.exports = nextConfig;
