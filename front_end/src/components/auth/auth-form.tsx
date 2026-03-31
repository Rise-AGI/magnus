// front_end/src/components/auth/auth-form.tsx
"use client";

import { useState } from "react";
import { LogIn, UserPlus, Lock } from "lucide-react";
import { useAuth } from "@/context/auth-context";
import { useLanguage } from "@/context/language-context";
import { useRouter } from "next/navigation";


type Tab = "login" | "register";


export function AuthForm() {
  const { login, loginWithPassword, register } = useAuth();
  const { t } = useLanguage();
  const router = useRouter();

  const [tab, setTab] = useState<Tab>("login");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  // Login form state
  const [loginName, setLoginName] = useState("");
  const [loginPassword, setLoginPassword] = useState("");

  // Register form state
  const [inviteCode, setInviteCode] = useState("");
  const [regName, setRegName] = useState("");
  const [regPassword, setRegPassword] = useState("");
  const [regConfirm, setRegConfirm] = useState("");

  const handlePasswordLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await loginWithPassword(loginName, loginPassword);
      router.push("/explorer");
    } catch (err: any) {
      setError(err.message || t("auth.loginFailed"));
    } finally {
      setLoading(false);
    }
  };

  const handleRegister = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");

    if (regPassword !== regConfirm) {
      setError(t("auth.passwordMismatch"));
      return;
    }

    setLoading(true);
    try {
      await register(inviteCode, regName, regPassword);
      router.push("/explorer");
    } catch (err: any) {
      setError(err.message || t("auth.loginFailed"));
    } finally {
      setLoading(false);
    }
  };

  const inputClass =
    "w-full px-4 py-2.5 bg-zinc-900 border border-zinc-700 rounded-lg text-zinc-100 text-sm placeholder-zinc-500 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500 transition-colors";

  return (
    <div className="w-full max-w-sm mx-auto">
      {/* Header */}
      <div className="text-center mb-8">
        <div className="w-14 h-14 bg-zinc-900 rounded-full flex items-center justify-center mx-auto mb-4 border border-zinc-800 shadow-xl">
          <Lock className="w-7 h-7 text-zinc-500" />
        </div>
        <h2 className="text-xl font-bold text-zinc-100">
          Magnus<span className="text-blue-500">Platform</span>
        </h2>
      </div>

      {/* Tabs */}
      <div className="flex mb-6 bg-zinc-900 rounded-lg p-1 border border-zinc-800">
        <button
          onClick={() => { setTab("login"); setError(""); }}
          className={`flex-1 py-2 text-sm font-medium rounded-md transition-all ${
            tab === "login"
              ? "bg-zinc-800 text-zinc-100 shadow-sm"
              : "text-zinc-500 hover:text-zinc-300"
          }`}
        >
          {t("auth.loginTab")}
        </button>
        <button
          onClick={() => { setTab("register"); setError(""); }}
          className={`flex-1 py-2 text-sm font-medium rounded-md transition-all ${
            tab === "register"
              ? "bg-zinc-800 text-zinc-100 shadow-sm"
              : "text-zinc-500 hover:text-zinc-300"
          }`}
        >
          {t("auth.registerTab")}
        </button>
      </div>

      {/* Error message */}
      {error && (
        <div className="mb-4 px-4 py-2.5 bg-red-900/30 border border-red-800/50 rounded-lg text-red-400 text-sm">
          {error}
        </div>
      )}

      {/* Login Tab */}
      {tab === "login" && (
        <div className="space-y-4">
          {/* Feishu Login */}
          <button
            onClick={login}
            className="w-full flex items-center justify-center gap-2 px-4 py-2.5 bg-blue-600 hover:bg-blue-500 text-white rounded-lg font-medium transition-all shadow-lg shadow-blue-900/20 hover:scale-[1.02] active:scale-[0.98]"
          >
            <LogIn className="w-4 h-4" />
            {t("auth.signInWithFeishu")}
          </button>

          {/* Divider */}
          <div className="flex items-center gap-3">
            <div className="flex-1 h-px bg-zinc-800" />
            <span className="text-xs text-zinc-600">{t("auth.orUsePassword")}</span>
            <div className="flex-1 h-px bg-zinc-800" />
          </div>

          {/* Password Login Form */}
          <form onSubmit={handlePasswordLogin} className="space-y-3">
            <input
              type="text"
              placeholder={t("auth.username")}
              value={loginName}
              onChange={(e) => setLoginName(e.target.value)}
              className={inputClass}
              required
            />
            <input
              type="password"
              placeholder={t("auth.password")}
              value={loginPassword}
              onChange={(e) => setLoginPassword(e.target.value)}
              className={inputClass}
              required
            />
            <button
              type="submit"
              disabled={loading}
              className="w-full py-2.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-200 rounded-lg font-medium text-sm transition-all border border-zinc-700 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {loading ? t("common.loading") : t("auth.loginWithPassword")}
            </button>
          </form>

          <p className="text-center text-xs text-zinc-600">
            {t("auth.noAccount")}{" "}
            <button onClick={() => { setTab("register"); setError(""); }} className="text-blue-500 hover:text-blue-400">
              {t("auth.registerTab")}
            </button>
          </p>
        </div>
      )}

      {/* Register Tab */}
      {tab === "register" && (
        <div className="space-y-4">
          <form onSubmit={handleRegister} className="space-y-3">
            <input
              type="text"
              placeholder={t("auth.inviteCode")}
              value={inviteCode}
              onChange={(e) => setInviteCode(e.target.value)}
              className={inputClass}
              required
            />
            <input
              type="text"
              placeholder={t("auth.username")}
              value={regName}
              onChange={(e) => setRegName(e.target.value)}
              className={inputClass}
              required
              minLength={1}
              maxLength={64}
            />
            <input
              type="password"
              placeholder={t("auth.password")}
              value={regPassword}
              onChange={(e) => setRegPassword(e.target.value)}
              className={inputClass}
              required
              minLength={8}
            />
            <input
              type="password"
              placeholder={t("auth.confirmPassword")}
              value={regConfirm}
              onChange={(e) => setRegConfirm(e.target.value)}
              className={inputClass}
              required
              minLength={8}
            />
            <button
              type="submit"
              disabled={loading}
              className="w-full flex items-center justify-center gap-2 py-2.5 bg-blue-600 hover:bg-blue-500 text-white rounded-lg font-medium text-sm transition-all shadow-lg shadow-blue-900/20 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <UserPlus className="w-4 h-4" />
              {loading ? t("common.loading") : t("auth.register")}
            </button>
          </form>

          <p className="text-center text-xs text-zinc-600">
            {t("auth.hasAccount")}{" "}
            <button onClick={() => { setTab("login"); setError(""); }} className="text-blue-500 hover:text-blue-400">
              {t("auth.loginTab")}
            </button>
          </p>
        </div>
      )}
    </div>
  );
}
