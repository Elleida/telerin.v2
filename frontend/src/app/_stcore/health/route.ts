import { NextResponse } from 'next/server';

// Silences repeated health-check requests from stale Streamlit browser clients
// that used to connect to this port before the app was replaced by Next.js.
export async function GET() {
  return NextResponse.json({ status: 'ok' });
}
