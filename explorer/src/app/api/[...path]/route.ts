import { type NextRequest, NextResponse } from "next/server";

const getBackendUrl = () =>
  process.env.BACKEND_URL || "http://localhost:4000";

async function proxyRequest(request: NextRequest, path: string) {
  const backendUrl = getBackendUrl();
  const destination = `${backendUrl}/api/explorer/${path}${request.nextUrl.search}`;

  const headers = new Headers(request.headers);
  headers.delete("host");

  const response = await fetch(destination, {
    method: request.method,
    headers,
    body: request.method !== "GET" && request.method !== "HEAD" ? request.body : undefined,
  });

  return new NextResponse(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers: response.headers,
  });
}

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> },
) {
  const { path } = await params;
  return proxyRequest(request, path.join("/"));
}

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> },
) {
  const { path } = await params;
  return proxyRequest(request, path.join("/"));
}

export async function PATCH(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> },
) {
  const { path } = await params;
  return proxyRequest(request, path.join("/"));
}

export async function DELETE(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> },
) {
  const { path } = await params;
  return proxyRequest(request, path.join("/"));
}
