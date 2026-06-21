// front_end/next.config.mjs
import fs from 'fs';
import path from 'path';
import yaml from 'js-yaml';
import { fileURLToPath } from 'url';


// 全栈统一配置注入环境
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const rootDir = path.resolve(__dirname, '..');
const configPath = process.env.MAGNUS_CONFIG_PATH || path.join(rootDir, 'configs', 'magnus_config.yaml');
const fileContents = fs.readFileSync(configPath, 'utf8');
const magnusConfig = yaml.load(fileContents);

const isDeliver = process.env.MAGNUS_DELIVER === 'TRUE';
if (!isDeliver) {
  console.log('⚠️ [NextConfig] Running in DEV mode. Hijacking magnusConfig.');
  magnusConfig.server.front_end_port += 2;
  magnusConfig.server.back_end_port += 2;
  magnusConfig.server.root += '-develop';
} else {
  console.log('🚀 [NextConfig] Running in DELIVERY mode.');
}

const serverAddress = magnusConfig.server.address;
const serverHost = new URL(serverAddress).host;


const authProvider = magnusConfig.server.auth.provider;

// 内存模式从 execution.slurm 注入 cluster 视图：per_cpu 站点（禁用 --mem，内存随核数
// 折算）前端据此把内存字段改为只读派生展示；explicit / local 站点维持原样可手填内存。
const slurmConfig = magnusConfig.execution?.slurm ?? {};
const clusterConfig = {
  ...magnusConfig.cluster,
  mem_mode: slurmConfig.mem_mode ?? 'explicit',
  mem_per_cpu_mb: slurmConfig.mem_per_cpu_mb ?? 4000,
};

/** @type {import('next').NextConfig} */
const nextConfig = {
  env: {
    NEXT_PUBLIC_FRONT_END_PORT: magnusConfig.server.front_end_port.toString(),
    NEXT_PUBLIC_BACK_END_PORT: magnusConfig.server.back_end_port.toString(),
    NEXT_PUBLIC_AUTH_PROVIDER: authProvider,
    ...(authProvider !== 'local' && magnusConfig.server.auth.feishu_client
      ? { NEXT_PUBLIC_FEISHU_APP_ID: magnusConfig.server.auth.feishu_client.app_id }
      : {}),
    NEXT_PUBLIC_POLL_INTERVAL: (magnusConfig.client?.jobs?.poll_interval ?? 2).toString(),
    NEXT_PUBLIC_SERVER_ADDRESS: serverAddress,
    NEXT_PUBLIC_CLUSTER_CONFIG: JSON.stringify(clusterConfig),
  },
  allowedDevOrigins: [
    `localhost:${magnusConfig.server.front_end_port}`,
    `${serverHost}:${magnusConfig.server.front_end_port}`,
  ],
};


export default nextConfig;