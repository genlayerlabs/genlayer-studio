import { type NextRequest, NextResponse } from "next/server";

const BACKEND_URL = process.env.BACKEND_URL || "http://localhost:4000";

export function proxy(request: NextRequest) {
  const { pathname, search } = request.nextUrl;

  // Let Next.js API routes handle these paths directly
  if (pathname.startsWith('/api/rpc') || pathname.startsWith('/api/health'))
    return NextResponse.next();

  const destination = `${BACKEND_URL}/api/explorer${pathname.slice("/api".length)}${search}`;
  return NextResponse.rewrite(new URL(destination));
}

export const config = {
  matcher: "/api/:path*",
};
