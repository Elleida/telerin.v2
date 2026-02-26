/** @type {import('next').NextConfig} */
const nextConfig = {
  // Serve the app under /teleradio (same path as the previous Streamlit app)
  basePath: '/teleradio',
  // Increase proxy timeout for long-running LLM calls (default is ~30s)
  experimental: {
    proxyTimeout: 120_000,
  },
  async rewrites() {
    return [
      {
        // Next.js auto-prepends basePath ('/teleradio') to this source,
        // so it matches '/teleradio/api/:path*'.
        // The frontend BASE in api.ts includes NEXT_PUBLIC_BASE_PATH so
        // fetch('/teleradio/api/...') is used — works both direct and via nginx.
        source: '/api/:path*',
        destination: `${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/:path*`,
      },
    ];
  },
};

module.exports = nextConfig;
