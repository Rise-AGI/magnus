// front_end/next.config.mjs
import fs from 'fs';
import path from 'path';
import yaml from 'js-yaml';
import { fileURLToPath } from 'url';


// 全栈统一配置注入环境
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const rootDir = path.resolve(__dirname, '..'); 
const configPath = path.join(rootDir, 'configs', 'magnus_config.yaml');
const fileContents = fs.readFileSync(configPath, 'utf8');
const magnusConfig = yaml.load(fileContents);


/** @type {import('next').NextConfig} */
const nextConfig = {
  env: {
    NEXT_PUBLIC_SERVER_PORT: magnusConfig.server.port.toString(),
    NEXT_PUBLIC_FEISHU_APP_ID: magnusConfig.server.feishu_client.app_id,
  }
};


export default nextConfig;