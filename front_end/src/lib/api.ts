// front_end/src/lib/api.ts
import { API_BASE } from "./config";

interface FetchOptions extends RequestInit {
  // 保持 RequestInit.body 的原始定义，避免 ts(2430) 错误
  // 新增 json 属性来接受原生对象，用于自动 JSON.stringify
  json?: Record<string, any>; 
}

/**
 * 统一的 API 客户端
 * 自动注入 Bearer Token, 自动 JSON.stringify(json), 处理 401 状态
 */
export async function client(endpoint: string, { json, body, ...customConfig }: FetchOptions = {}) {
  // 1. 处理 Token
  const token = typeof window !== "undefined" ? localStorage.getItem("magnus_token") : null;
  
  const headers: HeadersInit = {};

  // FormData needs the browser to auto-set Content-Type with boundary
  if (json) {
    (headers as any)["Content-Type"] = "application/json";
  } else if (!(body instanceof FormData)) {
    (headers as any)["Content-Type"] = "application/json";
  }

  if (token) {
    (headers as any)["Authorization"] = `Bearer ${token}`; 
  }

  // 2. 合并配置
  const config: RequestInit = {
    // 默认如果有 json 或 body，则为 POST
    method: (json || body) ? "POST" : "GET", 
    ...customConfig,
    headers: {
      ...headers,
      ...customConfig.headers,
    },
  };

  // 如果提供了 json 对象，则进行序列化
  if (json) {
    config.body = JSON.stringify(json);
  } else if (body) {
    // 否则使用原始的 body (例如 FormData 或 Blob)
    config.body = body as BodyInit;
  }

  // 3. 拼接 URL (处理 endpoint 开头的斜杠问题)
  const url = `${API_BASE}${endpoint.startsWith("/") ? endpoint : `/${endpoint}`}`;

  try {
    const response = await fetch(url, config);

    // 4. 全局 401 (未授权/Token过期) 拦截
    if (response.status === 401) {
      if (typeof window !== "undefined") {
        localStorage.removeItem("magnus_token");
        localStorage.removeItem("magnus_user");
        window.dispatchEvent(new Event("magnus-auth-change"));
      }
      return Promise.reject(new Error("Unauthorized"));
    }

    // 5. 处理通用业务错误
    if (!response.ok) {
      // 尝试解析错误详情
      const errorData = await response.json().catch(() => ({})); 
      throw new Error(errorData.detail || `API Error: ${response.statusText}`);
    }

    // 返回解析后的 JSON（204 No Content 无 body）
    if (response.status === 204) return null;
    return response.json();
    
  } catch (error) {
    console.error("API Request Failed:", error);
    throw error;
  }
}