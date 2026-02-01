// front_end/src/lib/config.ts

function requireEnv(value: string | undefined, key: string): string {
  if (!value) {
    throw new Error(`❌ 致命错误: 环境变量 ${key} 未定义，请检查配置文件`);
  }
  return value;
}

// API_BASE 使用空字符串，所有 /api/... 请求通过 Next.js API Routes 代理到后端
// 这样无论是 HTTP 本地开发还是 HTTPS 公网访问都不会有 Mixed Content 问题
// SDK 和其他客户端只需要配置前端地址即可
export const API_BASE = "";
export const FEISHU_APP_ID = requireEnv(
    process.env.NEXT_PUBLIC_FEISHU_APP_ID, 
    "NEXT_PUBLIC_FEISHU_APP_ID",
);
export const POLL_INTERVAL = parseInt(
  requireEnv(
    process.env.NEXT_PUBLIC_POLL_INTERVAL, 
    "NEXT_PUBLIC_POLL_INTERVAL"
  ),
  10,
) * 1000;

export interface GpuConfig {
  value: string;
  label: string;
  meta: string;
  limit: number;
}

export interface ClusterResources {
  gpus: GpuConfig[];
  cpus: {
    max_count: number;
  };
  memory: {
    default_limit: string;
  };
  runner: {
    default_user: string;
  };
}

export interface ClusterConfig {
  name: string;
  resources: ClusterResources;
}

const clusterConfigJson = requireEnv(
  process.env.NEXT_PUBLIC_CLUSTER_CONFIG,
  "NEXT_PUBLIC_CLUSTER_CONFIG"
);

let parsedConfig: ClusterConfig;
try {
  parsedConfig = JSON.parse(clusterConfigJson);
} catch (e) {
  throw new Error(`❌ 致命错误: 集群配置 JSON 解析失败。请检查 magnus_config.yaml 内容是否合法。\n错误信息: ${(e as Error).message}`);
}

if (!parsedConfig.resources || !parsedConfig.resources.gpus || !parsedConfig.resources.cpus) {
  throw new Error(`❌ 致命错误: 集群配置结构不完整。请确保 yaml 包含 resources.gpus 和 resources.cpus`);
}

export const CLUSTER_CONFIG = parsedConfig;

export const PHYSICAL_GPUS = CLUSTER_CONFIG.resources.gpus;
export const MAX_CPU_COUNT = CLUSTER_CONFIG.resources.cpus.max_count;
export const DEFAULT_MEMORY = CLUSTER_CONFIG.resources.memory.default_limit;
export const DEFAULT_RUNNER = CLUSTER_CONFIG.resources.runner.default_user;
export function getGpuLimit(gpuType: string): number {
  if (gpuType === 'cpu') return 0;
  const gpu = PHYSICAL_GPUS.find(g => g.value === gpuType);
  if (!gpu) {
    console.error(`❌ 配置错误: 前端请求了不存在的 GPU 类型 '${gpuType}'。请检查代码中的 GPU 类型是否在配置文件中定义。`);
    return 1;
  }
  return gpu.limit;
}