import assert from "node:assert/strict";
import http from "node:http";
import { once } from "node:events";
import { Readable } from "node:stream";
import { gzipSync } from "node:zlib";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { after, before, test } from "node:test";
import ts from "typescript";

// Execute the production helper without starting Next or a Magnus database.
const source = readFileSync(new URL("../src/lib/backend-proxy.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
}).outputText;
const { proxyToBackend } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);
let backend;
let proxy;
let backendBase;
let proxyPort;
const hits = [];

before(async () => {
  backend = http.createServer(async (req, res) => {
    const hash = createHash("sha256");
    let bytes = 0;
    for await (const chunk of req) { bytes += chunk.length; hash.update(chunk); }
    const received = { url: req.url, headers: req.headers, bytes, sha256: hash.digest("hex") };
    hits.push(received);
    if (req.url.startsWith("/api/unauthorized")) {
      res.writeHead(401, { "www-authenticate": "Bearer", "content-type": "application/json" });
      res.end('{"detail":"Not authenticated"}');
    } else if (req.url === "/api/redirect") {
      res.writeHead(307, { location: `${backendBase}/api/destination?x=1` });
      res.end("redirect");
    } else if (req.url === "/api/external") {
      res.writeHead(302, { location: "https://example.org/login" });
      res.end();
    } else if (req.url === "/api/double-slash") {
      res.writeHead(307, { location: `${backendBase}//example.org/path` }); res.end();
    } else if (req.url === "/api/cookies") {
      res.writeHead(200, {
        "set-cookie": ["first=1; HttpOnly", "second=2; Expires=Wed, 21 Oct 2037 07:28:00 GMT"],
        connection: "keep-alive, x-hop-response", "x-hop-response": "remove",
      });
      res.end("cookies");
    } else if (req.url === "/api/compressed") {
      const body = gzipSync(Buffer.from("decoded result"));
      res.writeHead(200, { "content-encoding": "gzip", "content-length": body.length });
      res.end(body);
    } else if (req.url === "/api/empty") {
      res.writeHead(204); res.end();
    } else {
      res.setHeader("content-type", "application/json");
      res.end(JSON.stringify(received));
    }
  });
  backend.listen(0, "127.0.0.1");
  await once(backend, "listening");
  backendBase = `http://127.0.0.1:${backend.address().port}`;
  proxy = http.createServer(async (req, res) => {
    const hasBody = !["GET", "HEAD"].includes(req.method);
    const request = new Request(`http://127.0.0.1:${proxyPort}${req.url}`, {
      method: req.method, headers: req.headers,
      ...(hasBody ? { body: Readable.toWeb(req), duplex: "half" } : {}),
    });
    const result = await proxyToBackend(request, backendBase);
    res.statusCode = result.status;
    for (const [name, value] of result.headers) if (name !== "set-cookie") res.setHeader(name, value);
    const cookies = result.headers.getSetCookie();
    if (cookies.length) res.setHeader("set-cookie", cookies);
    if (result.body) Readable.fromWeb(result.body).pipe(res);
    else res.end();
  });
  proxy.listen(0, "127.0.0.1");
  await once(proxy, "listening");
  proxyPort = proxy.address().port;
});

after(() => {
  proxy?.closeAllConnections(); proxy?.close();
  backend?.closeAllConnections(); backend?.close();
});

function post(path, body, headers = {}, waitForContinue = false) {
  return new Promise((resolve, reject) => {
    let continued = false;
    const request = http.request({ host: "127.0.0.1", port: proxyPort, path, method: "POST", headers }, (response) => {
      let text = "";
      response.on("data", (chunk) => { text += chunk; });
      response.on("end", () => resolve({ status: response.statusCode, headers: response.headers, text, continued }));
      response.on("error", reject);
    });
    request.setTimeout(10000, () => request.destroy(new Error("test timeout")));
    request.on("error", reject);
    if (waitForContinue) {
      request.on("continue", () => { continued = true; request.end(body); });
      request.flushHeaders();
    } else {
      request.write(body.subarray(0, 1)); request.end(body.subarray(1));
    }
  });
}

test("100-continue uploads preserve bytes and authentication", async () => {
  const body = Buffer.alloc(2 * 1024 * 1024, 0x5a);
  const result = await post("/api/files/upload", body, {
    expect: "100-continue", "content-length": body.length,
    authorization: "Bearer test-only", cookie: "session=test-only",
    connection: "keep-alive, X-Hop-Test", "x-hop-test": "remove",
  }, true);
  assert.equal(result.status, 200);
  assert.equal(result.continued, true);
  const received = JSON.parse(result.text);
  assert.equal(received.bytes, body.length);
  assert.equal(received.sha256, createHash("sha256").update(body).digest("hex"));
  assert.equal(received.headers.expect, undefined);
  assert.equal(received.headers["x-hop-test"], undefined);
  assert.equal(received.headers.authorization, "Bearer test-only");
  assert.equal(received.headers.cookie, "session=test-only");
  assert.equal(received.headers["x-request-id"], result.headers["x-request-id"]);
});

test("caller cancellation is identified without replaying an upload", async () => {
  const abort = new AbortController();
  abort.abort(new Error("private caller reason"));
  const before = hits.length;
  const result = await proxyToBackend(new Request("http://public/api/files/upload", {
    method: "POST", body: "private payload", signal: abort.signal,
  }), backendBase);
  assert.equal(result.status, 499);
  assert.equal(hits.length, before);
  const body = await result.text();
  assert.ok(!body.includes("private"));
  assert.ok(result.headers.get("x-request-id"));
});

test("chunked uploads and escaped paths are forwarded without buffering", async () => {
  const result = await post("/api/a%2Fb?query=%2F", Buffer.from("chunked payload"));
  assert.equal(result.status, 200);
  assert.equal(JSON.parse(result.text).url, "/api/a%2Fb?query=%2F");
  assert.equal(JSON.parse(result.text).bytes, 15);
});

test("401 body and authentication challenge survive streaming POST", async () => {
  const result = await post("/api/unauthorized", Buffer.from("request"));
  assert.equal(result.status, 401);
  assert.equal(result.headers["www-authenticate"], "Bearer");
  assert.equal(JSON.parse(result.text).detail, "Not authenticated");
});

test("redirects are returned without replay and backend URLs remain on the public origin", async () => {
  const before = hits.length;
  const result = await post("/api/redirect", Buffer.from("request"));
  assert.equal(result.status, 307);
  assert.equal(result.headers.location, "/api/destination?x=1");
  assert.equal(hits.length - before, 1);
  const external = await post("/api/external", Buffer.from("request"));
  assert.equal(external.headers.location, "https://example.org/login");
  const doubled = await post("/api/double-slash", Buffer.from("request"));
  assert.equal(new URL(doubled.headers.location, "https://public.invalid").origin, "https://public.invalid");
});

test("response connection fields are removed and multiple cookies remain separate", async () => {
  const result = await post("/api/cookies", Buffer.from("request"));
  assert.equal(result.status, 200);
  assert.equal(result.headers["x-hop-response"], undefined);
  assert.equal(result.headers["set-cookie"].length, 2);
});

test("decoded responses remove compressed length; HEAD and 204 stay bodyless", async () => {
  const result = await post("/api/compressed", Buffer.from("request"));
  assert.equal(result.text, "decoded result");
  assert.equal(result.headers["content-encoding"], undefined);
  assert.notEqual(result.headers["content-length"], String(gzipSync(Buffer.from("decoded result")).length));
  for (const [method, path, status] of [["HEAD", "/api/echo", 200], ["GET", "/api/empty", 204]]) {
    const response = await proxyToBackend(new Request(`https://public.invalid${path}`, { method }), backendBase);
    assert.equal(response.status, status);
    assert.equal(response.body, null);
  }
});

test("connection failures return correlated safe 502 details", async () => {
  const response = await proxyToBackend(new Request("https://public.invalid/api/files/download/private-token"), "http://127.0.0.1:1");
  assert.equal(response.status, 502);
  const result = await response.json();
  assert.equal(result.request_id, response.headers.get("x-request-id"));
  assert.ok(!JSON.stringify(result).includes("private-token"));
});
