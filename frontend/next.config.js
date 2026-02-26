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
        // basePath: false → don't auto-prepend '/teleradio' to source,
        // so fetch('/api/...') from the browser still matches this rule
        // (otherwise Next.js would require '/teleradio/api/...')
        source: '/api/:path*',
        basePath: false,
        destination: `${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/:path*`,
      },
    ];
  },
};

module.exports = nextConfig;
