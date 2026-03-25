// front_end/server.mjs
// Production server: Next.js HTTP + WebSocket proxy to backend
import http from 'node:http';
import { parse, fileURLToPath } from 'node:url';
import fs from 'node:fs';
import path from 'node:path';
import next from 'next';
import yaml from 'js-yaml';


// Read backend port from yaml (same logic as next.config.mjs)
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const rootDir = path.resolve(__dirname, '..');
const configPath = process.env.MAGNUS_CONFIG_PATH || path.join(rootDir, 'configs', 'magnus_config.yaml');
const magnusConfig = yaml.load(fs.readFileSync(configPath, 'utf8'));

if (process.env.MAGNUS_DELIVER !== 'TRUE') {
  magnusConfig.server.front_end_port += 2;
  magnusConfig.server.back_end_port += 2;
}
const BACKEND_PORT = magnusConfig.server.back_end_port;

// Parse CLI args: npm run start -- -p 3011 -H 0.0.0.0
let port = magnusConfig.server.front_end_port;
let hostname = '0.0.0.0';
for (let i = 2; i < process.argv.length; i++) {
  if (process.argv[i] === '-p' && process.argv[i + 1]) port = parseInt(process.argv[++i]);
  else if (process.argv[i] === '-H' && process.argv[i + 1]) hostname = process.argv[++i];
}

// Next.js
const app = next({ dev: false });
const handle = app.getRequestHandler();
await app.prepare();

const server = http.createServer((req, res) => {
  handle(req, res, parse(req.url, true));
});

// WebSocket proxy: /ws/* → backend
server.on('upgrade', (req, socket, head) => {
  if (!req.url.startsWith('/ws/')) {
    socket.destroy();
    return;
  }

  const proxyReq = http.request({
    hostname: '127.0.0.1',
    port: BACKEND_PORT,
    path: req.url,
    method: req.method,
    headers: req.headers,
  });

  proxyReq.on('upgrade', (proxyRes, proxySocket, proxyHead) => {
    let header = 'HTTP/1.1 101 Switching Protocols\r\n';
    for (const [key, value] of Object.entries(proxyRes.headers)) {
      header += `${key}: ${value}\r\n`;
    }
    header += '\r\n';
    socket.write(header);

    if (proxyHead.length > 0) socket.write(proxyHead);
    if (head.length > 0) proxySocket.write(head);

    proxySocket.pipe(socket);
    socket.pipe(proxySocket);

    proxySocket.on('error', () => socket.destroy());
    socket.on('error', () => proxySocket.destroy());
  });

  proxyReq.on('error', (err) => {
    console.error('[WS Proxy] error:', err.message);
    socket.destroy();
  });

  proxyReq.end();
});

server.listen(port, hostname, () => {
  console.log(`🚀 [Magnus] Production server on ${hostname}:${port} (WS proxy → :${BACKEND_PORT})`);
});
