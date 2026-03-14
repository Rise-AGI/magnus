// front_end/src/context/auth-context.tsx
"use client";


import React, { createContext, useContext, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { User } from "@/types/auth";
import { FEISHU_APP_ID, IS_LOCAL_MODE, API_BASE } from "@/lib/config";


interface AuthContextType {
  user: User | null;
  isLoading: boolean;
  login: () => void;
  logout: () => void;
}


const AuthContext = createContext<AuthContextType | undefined>(undefined);


export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const router = useRouter();

  // 读取并解析本地存储的用户信息
  const loadUserFromStorage = () => {
    try {
      const storedUser = localStorage.getItem("magnus_user");
      const token = localStorage.getItem("magnus_token");

      if (token && storedUser) {
        setUser(JSON.parse(storedUser));
      } else {
        setUser(null);
      }
    } catch (error) {
      console.error("Failed to parse user data", error);
      setUser(null);
    } finally {
      setIsLoading(false);
    }
  };

  // 本地模式免登录：自动获取 JWT
  const autoLoginLocal = async () => {
    try {
      const resp = await fetch(`${API_BASE}/api/auth/local/login`, { method: "POST" });
      if (!resp.ok) {
        console.error("Local auto-login failed:", resp.status);
        setIsLoading(false);
        return;
      }
      const data = await resp.json();
      localStorage.setItem("magnus_token", data.access_token);
      localStorage.setItem("magnus_user", JSON.stringify(data.user));
      setUser(data.user);
    } catch (error) {
      console.error("Local auto-login error:", error);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    // 1. 尝试从 localStorage 读取已有的登录信息
    const storedUser = localStorage.getItem("magnus_user");
    const storedToken = localStorage.getItem("magnus_token");

    if (storedToken && storedUser) {
      try {
        setUser(JSON.parse(storedUser));
      } catch {
        setUser(null);
      }
      setIsLoading(false);
    } else if (IS_LOCAL_MODE) {
      // 2. 本地模式下没有 token 时自动登录
      autoLoginLocal();
    } else {
      setIsLoading(false);
    }

    // 3. 监听登录事件 (由 Callback 页面触发)
    const handleAuthChange = () => {
      loadUserFromStorage();
    };

    window.addEventListener("magnus-auth-change", handleAuthChange);

    return () => {
      window.removeEventListener("magnus-auth-change", handleAuthChange);
    };
  }, []);

  const login = () => {
    if (IS_LOCAL_MODE) {
      autoLoginLocal();
      return;
    }
    const REDIRECT_URI = `${window.location.origin}/auth/callback`;
    const FEISHU_AUTH_URL = `https://open.feishu.cn/open-apis/authen/v1/authorize?app_id=${FEISHU_APP_ID}&redirect_uri=${encodeURIComponent(REDIRECT_URI)}&state=RANDOM_STATE`;

    window.location.href = FEISHU_AUTH_URL;
  };

  const logout = () => {
    localStorage.removeItem("magnus_token");
    localStorage.removeItem("magnus_user");
    setUser(null);
    router.push("/"); // 登出后回首页
  };

  return (
    <AuthContext.Provider value={{ user, isLoading, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}


// 自定义 Hook，方便组件调用
export function useAuth() {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return context;
}