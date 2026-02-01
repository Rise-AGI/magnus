// front_end/src/app/api/[...path]/route.ts
// 反向代理：统一入口，解决 Mixed Content 问题
// 用户只需知道前端地址，SDK 配置 MAGNUS_ADDRESS 为前端地址即可
// 浏览器/SDK -> Next.js (同源) -> 后端 (内网 HTTP)

import { NextRequest, NextResponse } from "next/server";

const BACKEND_BASE = `${process.env.NEXT_PUBLIC_SERVER_ADDRESS}:${process.env.NEXT_PUBLIC_BACK_END_PORT}`;

async function proxyRequest(request: NextRequest, path: string[]) {
  const targetPath = path.join("/");
  const targetUrl = new URL(`/${targetPath}`, BACKEND_BASE);
  targetUrl.search = request.nextUrl.search;

  const headers = new Headers();
  request.headers.forEach((value, key) => {
    if (key.toLowerCase() !== "host") {
      headers.set(key, value);
    }
  });

  const body = request.method !== "GET" && request.method !== "HEAD"
    ? await request.arrayBuffer()
    : undefined;

  const response = await fetch(targetUrl.toString(), {
    method: request.method,
    headers,
    body,
  });

  const responseHeaders = new Headers();
  response.headers.forEach((value, key) => {
    if (!["content-encoding", "transfer-encoding"].includes(key.toLowerCase())) {
      responseHeaders.set(key, value);
    }
  });

  return new NextResponse(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers: responseHeaders,
  });
}

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> },
) {
  const { path } = await params;
  return proxyRequest(request, path);
}

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> },
) {
  const { path } = await params;
  return proxyRequest(request, path);
}

export async function PUT(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> },
) {
  const { path } = await params;
  return proxyRequest(request, path);
}

export async function PATCH(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> },
) {
  const { path } = await params;
  return proxyRequest(request, path);
}

export async function DELETE(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> },
) {
  const { path } = await params;
  return proxyRequest(request, path);
}
