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
      {
        // Proxy static PNG images through Next.js so they work behind nginx
        // without needing port 8000 to be externally reachable, and avoid
        // mixed-content issues (https page -> http:8000 image).
        // Browser requests /teleradio/images/* → Next.js → localhost:8000/images/*
        source: '/images/:path*',
        destination: `${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/images/:path*`,
      },
    ];
  },
};

module.exports = nextConfig;
