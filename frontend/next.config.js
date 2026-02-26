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
    return [
      {
        source: '/api/:path*',
        destination: `${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/:path*`,
      },
      {
        source: '/ws/:path*',
        destination: `${wsBase}/ws/:path*`,
      },
    ];
  },
};

module.exports = nextConfig;
