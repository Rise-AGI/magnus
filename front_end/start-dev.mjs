// front_end/start-dev.mjs
import fs from 'fs';
import path from 'path';
import yaml from 'js-yaml';
import { fileURLToPath } from 'url';
import { spawn } from 'child_process';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const rootDir = path.resolve(__dirname, '..'); 
const configPath = path.join(rootDir, 'configs', 'magnus_config.yaml');

const fileContents = fs.readFileSync(configPath, 'utf8');
const magnusConfig = yaml.load(fileContents);

const PORT = magnusConfig.server.front_end_port + 2;
const BACK_END_PORT = magnusConfig.server.back_end_port + 2;

console.log(`[Magnus Dev] Starting development server on port ${PORT}...`);

const cmd = 'next';
const args = ['dev', '-p', PORT, '-H', '0.0.0.0'];

const child = spawn(cmd, args, {
    stdio: 'inherit',
    shell: true,
    env: { ...process.env, NEXT_PUBLIC_BACK_END_PORT: String(BACK_END_PORT) },
});

child.on('close', (code) => {
    process.exit(code);
});