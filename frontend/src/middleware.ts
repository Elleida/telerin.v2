import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';

/**
 * Intercepts /_stcore/* requests from stale Streamlit browser sessions
 * that keep polling the port after the Streamlit app was replaced by Next.js.
 * Returning valid JSON stops the client retry loop without spamming 404s.
 * WebSocket upgrade requests are rejected with 400 to avoid a Next.js crash
 * (Next.js cannot proxy WS upgrades).
 */
export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  // Reject WebSocket upgrades cleanly (avoid "Cannot read properties of undefined" crash)
  if (request.headers.get('upgrade')?.toLowerCase() === 'websocket') {
    return new NextResponse('WebSocket not supported on this path', { status: 400 });
  }

  if (pathname === '/_stcore/health') {
    return NextResponse.json({ status: 'ok' });
  }

  if (pathname === '/_stcore/host-config') {
    return NextResponse.json({
      allowedOrigins: [],
      customTheme: null,
      forceAutoReconnect: false,
    });
  }

  return NextResponse.next();
}

export const config = {
  matcher: ['/_stcore/:path*'],
};
