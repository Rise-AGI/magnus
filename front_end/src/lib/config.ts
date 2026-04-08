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
export const DEFAULT_ROUTE = "/jobs";
export const AUTH_PROVIDER = process.env.NEXT_PUBLIC_AUTH_PROVIDER ?? "feishu";
export const IS_LOCAL_MODE = AUTH_PROVIDER === "local";
export const FEISHU_APP_ID = IS_LOCAL_MODE
    ? ""
    : requireEnv(
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

export interface ClusterConfig {
  name: string;
  gpus: GpuConfig[];
  max_cpu_count: number;
  max_memory_demand: string;
  default_cpu_count: number;
  default_memory_demand: string;
  default_runner: string;
  default_container_image: string;
  default_ephemeral_storage: string;
  default_system_entry_command: string;
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

if (!parsedConfig.gpus) {
  throw new Error(`❌ 致命错误: 集群配置结构不完整。请确保 yaml 包含 cluster.gpus`);
}

export const CLUSTER_CONFIG = parsedConfig;

export const PHYSICAL_GPUS = CLUSTER_CONFIG.gpus;
export const MAX_CPU_COUNT = CLUSTER_CONFIG.max_cpu_count;
export const DEFAULT_CPU_COUNT = CLUSTER_CONFIG.default_cpu_count;
export const DEFAULT_MEMORY = CLUSTER_CONFIG.default_memory_demand;
export const DEFAULT_RUNNER = CLUSTER_CONFIG.default_runner;
export const DEFAULT_CONTAINER_IMAGE = CLUSTER_CONFIG.default_container_image;
export const DEFAULT_EPHEMERAL_STORAGE = CLUSTER_CONFIG.default_ephemeral_storage;
export const DEFAULT_SYSTEM_ENTRY_COMMAND = CLUSTER_CONFIG.default_system_entry_command;
export function getGpuLimit(gpuType: string): number {
  if (gpuType === 'cpu') return 0;
  const gpu = PHYSICAL_GPUS.find(g => g.value === gpuType);
  if (!gpu) {
    console.error(`❌ 配置错误: 前端请求了不存在的 GPU 类型 '${gpuType}'。请检查代码中的 GPU 类型是否在配置文件中定义。`);
    return 1;
  }
  return gpu.limit;
}
