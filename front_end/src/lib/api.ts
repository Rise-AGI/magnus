// front_end/src/lib/api.ts
import { API_BASE } from "./config";

interface FetchOptions extends RequestInit {
  // 预留接口，如果未来需要支持 params (查询参数) 可以加在这里
}

/**
 * 统一的 API 客户端
 * 自动注入 Bearer Token，处理 401 状态
 */
export async function client(endpoint: string, { body, ...customConfig }: FetchOptions = {}) {
  // 1. 处理 Token
  const token = typeof window !== "undefined" ? localStorage.getItem("magnus_token") : null;
  
  const headers: HeadersInit = {
    "Content-Type": "application/json",
  };

  if (token) {
    (headers as any)["Authorization"] = `Bearer ${token}`;
  }

  // 2. 合并配置
  const config: RequestInit = {
    method: body ? "POST" : "GET", // 默认如果有 body 就是 POST
    ...customConfig,
    headers: {
      ...headers,
      ...customConfig.headers,
    },
  };

  if (body) {
    config.body = JSON.stringify(body);
  }

  // 3. 拼接 URL (处理 endpoint 开头的斜杠问题)
  const url = `${API_BASE}${endpoint.startsWith("/") ? endpoint : `/${endpoint}`}`;

  console.log(`📡 Request: ${config.method} ${url}`);

  try {
    const response = await fetch(url, config);

    // 4. 全局 401 (未授权/Token过期) 拦截
    if (response.status === 401) {
      console.warn("🔒 Token expired or unauthorized. Logging out...");
      if (typeof window !== "undefined") {
        localStorage.removeItem("magnus_token");
        localStorage.removeItem("magnus_user");
        // 触发事件通知 AuthContext 更新状态
        window.dispatchEvent(new Event("magnus-auth-change"));
      }
      return Promise.reject(new Error("Unauthorized"));
    }

    // 5. 处理通用业务错误
    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.detail || `API Error: ${response.statusText}`);
    }

    // 返回解析后的 JSON
    return response.json();
    
  } catch (error) {
    console.error("❌ API Request Failed:", error);
    throw error;
  }
}