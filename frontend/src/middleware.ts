import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';

/**
 * Intercepts /_stcore/* requests from stale Streamlit browser sessions
 * that keep polling the port after the Streamlit app was replaced by Next.js.
 * Returning valid JSON stops the client retry loop without spamming 404s.
 */
export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

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
