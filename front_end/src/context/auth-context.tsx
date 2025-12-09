// front_end/src/context/auth-context.tsx
"use client";


import React, { createContext, useContext, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { User } from "@/types/auth";
import { FEISHU_APP_ID } from "@/lib/config";


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

  useEffect(() => {
    // 1. 初始化时读取
    loadUserFromStorage();

    // 2. 监听登录事件 (由 Callback 页面触发)
    const handleAuthChange = () => {
      loadUserFromStorage();
    };

    window.addEventListener("magnus-auth-change", handleAuthChange);

    return () => {
      window.removeEventListener("magnus-auth-change", handleAuthChange);
    };
  }, []);

  const login = () => {
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