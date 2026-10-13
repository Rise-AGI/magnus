// Browser/SDK -> same-origin HTTP proxy -> backend.
import { proxyToBackend } from "@/lib/backend-proxy";

function getBackendPort(): string {
  const port = process.env.NEXT_PUBLIC_BACK_END_PORT;
  if (!port) throw new Error("NEXT_PUBLIC_BACK_END_PORT is not set");
  return port;
}

const BACKEND_BASE = `http://127.0.0.1:${getBackendPort()}`;

function proxyRequest(request: Request): Promise<Response> {
  return proxyToBackend(request, BACKEND_BASE);
}

export {
  proxyRequest as GET,
  proxyRequest as HEAD,
  proxyRequest as POST,
  proxyRequest as PUT,
  proxyRequest as PATCH,
  proxyRequest as DELETE,
  proxyRequest as OPTIONS,
};
