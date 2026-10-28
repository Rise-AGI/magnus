import { randomUUID } from "node:crypto";

const CONNECTION_HEADERS = new Set([
  "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
  "proxy-connection", "te", "trailer", "transfer-encoding", "upgrade",
]);

function forwardedHeaders(source: Headers): Headers {
  // RFC 9110 §7.6.1: Connection may name additional fields for this hop only.
  const excluded = new Set(CONNECTION_HEADERS);
  for (const name of (source.get("connection") ?? "").split(",")) {
    excluded.add(name.trim().toLowerCase());
  }
  const headers = new Headers();
  source.forEach((value, name) => {
    if (!excluded.has(name) && name !== "set-cookie") headers.set(name, value);
  });
  if (!excluded.has("set-cookie")) {
    for (const cookie of source.getSetCookie()) headers.append("set-cookie", cookie);
  }
  return headers;
}

function failureCode(error: unknown, signal: AbortSignal): string {
  if (signal.aborted) return "CLIENT_DISCONNECTED";
  let current = error;
  for (let depth = 0; current && depth < 4; depth++) {
    const value = current as { code?: unknown; name?: unknown; cause?: unknown };
    if (typeof value.code === "string" && /^[A-Z0-9_]{1,64}$/.test(value.code)) return value.code;
    if (value.name === "AbortError" || value.name === "ResponseAborted") return "REQUEST_ABORTED";
    current = value.cause;
  }
  return "UPSTREAM_FAILURE";
}

/** Stream one HTTP hop without replaying uploads or forwarding connection state. */
export async function proxyToBackend(
  request: Request,
  backendBase: string,
): Promise<Response> {
  const incomingUrl = new URL(request.url);
  const targetUrl = new URL(backendBase);
  // Assignment keeps even // paths on the configured backend and retains escapes.
  targetUrl.pathname = incomingUrl.pathname;
  targetUrl.search = incomingUrl.search;
  const requestId = randomUUID();
  const headers = forwardedHeaders(request.headers);
  headers.delete("host");
  // The incoming Node server handles 100-continue; fetch cannot forward Expect.
  headers.delete("expect");
  headers.set("x-request-id", requestId);
  headers.set("accept-encoding", "identity");
  const hasBody = request.method !== "GET" && request.method !== "HEAD";
  const observed = incomingUrl.pathname === "/api/files/upload"
    || incomingUrl.pathname.startsWith("/api/files/uploads");
  const started = performance.now();
  let bytes = 0;
  let lastProgress = started;
  const ray = request.headers.get("cf-ray") ?? "";
  const event = (phase: string, extra = {}) => {
    if (observed) console.info("[Upload proxy]", {
      utc: new Date().toISOString(), request_id: requestId,
      cf_ray: /^[A-Za-z0-9-]{8,80}$/.test(ray) ? ray : undefined,
      method: request.method, phase, body_bytes: bytes,
      elapsed_ms: Math.round(performance.now() - started), ...extra,
    });
  };
  event("headers");
  const body = hasBody && observed && request.body
    ? request.body.pipeThrough(new TransformStream<Uint8Array, Uint8Array>({
      transform(chunk, controller) {
        bytes += chunk.byteLength;
        if (performance.now() - lastProgress >= 10000) {
          event("forwarding"); lastProgress = performance.now();
        }
        controller.enqueue(chunk);
      },
      // Draining the source is not a remote disk acknowledgement.
      flush() { event("request_body_drained"); },
    })) : hasBody ? request.body : undefined;

  try {
    const response = await fetch(targetUrl, {
      method: request.method,
      headers,
      body,
      // @ts-expect-error Node fetch requires duplex for a streaming request body.
      duplex: hasBody ? "half" : undefined,
      signal: request.signal,
      cache: "no-store",
      // The caller owns authentication and redirects. Automatic handling can
      // replay a consumed stream (including on 401 in newer Undici versions).
      credentials: "omit",
      redirect: "manual",
    });
    event("upstream_response", { status: response.status });
    const responseHeaders = forwardedHeaders(response.headers);
    responseHeaders.set("x-request-id", requestId);
    const encoding = response.headers.get("content-encoding");
    if (response.body && encoding && encoding.split(",").every(
      (name) => ["gzip", "x-gzip", "deflate", "x-deflate", "br"].includes(name.trim().toLowerCase()),
    )) {
      // A backend may ignore identity. Fetch decodes these encodings, so its
      // compressed Content-Length no longer describes the response body.
      responseHeaders.delete("content-encoding");
      responseHeaders.delete("content-length");
    }
    const location = responseHeaders.get("location");
    if (location) {
      const destination = new URL(location, targetUrl);
      if (destination.origin === targetUrl.origin) {
        // A same-origin // path must not become a network-path redirect.
        const pathname = destination.pathname.startsWith("//")
          ? `/.${destination.pathname}` : destination.pathname;
        responseHeaders.set("location", `${pathname}${destination.search}${destination.hash}`);
      }
    }
    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: responseHeaders,
    });
  } catch (error) {
    const code = failureCode(error, request.signal);
    const timeout = ["UND_ERR_HEADERS_TIMEOUT", "UND_ERR_BODY_TIMEOUT", "ETIMEDOUT"].includes(code);
    // URLs can contain custody secrets; log neither URLs nor arbitrary upstream
    // messages, credentials or request bodies.
    event("interrupted", { code });
    console.error("[API proxy]", { utc: new Date().toISOString(), request_id: requestId, method: request.method, code });
    return Response.json({
      detail: timeout ? "Backend response timed out." : "Could not communicate with the backend.",
      request_id: requestId,
    }, {
      status: code === "CLIENT_DISCONNECTED" ? 499 : timeout ? 504 : 502,
      headers: { "x-request-id": requestId, "cache-control": "no-store" },
    });
  }
}
