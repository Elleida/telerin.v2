import { NextResponse } from 'next/server';

export async function GET() {
  return NextResponse.json({
    allowedOrigins: [],
    customTheme: null,
    forceAutoReconnect: false,
  });
}
