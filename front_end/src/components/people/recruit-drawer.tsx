// front_end/src/components/people/recruit-drawer.tsx
"use client";

import { useState, useRef } from "react";
import { Plus, Camera, Loader2, AlertTriangle } from "lucide-react";
import { client } from "@/lib/api";
import { Drawer } from "@/components/ui/drawer";
import { CopyableText } from "@/components/ui/copyable-text";
import { useLanguage } from "@/context/language-context";


interface RecruitDrawerProps {
  isOpen: boolean;
  onClose: () => void;
  onSuccess: () => void;
}


export function RecruitDrawer({ isOpen, onClose, onSuccess }: RecruitDrawerProps) {
  const { t } = useLanguage();
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [name, setName] = useState("");
  const [avatarFile, setAvatarFile] = useState<File | null>(null);
  const [avatarPreview, setAvatarPreview] = useState<string | null>(null);
  const [isRecruiting, setIsRecruiting] = useState(false);
  const [connector, setConnector] = useState<"general" | "openclaw">("general");

  const [errorField, setErrorField] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  // Post-create credentials display
  const [showCredentials, setShowCredentials] = useState(false);
  const [credentials, setCredentials] = useState<{ token: string; app_secret: string } | null>(null);

  const clearError = (field: string) => {
    if (errorField === field) { setErrorField(null); setErrorMessage(null); }
  };

  const scrollToError = (id: string) => {
    const el = document.getElementById(id);
    if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
  };

  const resetAndClose = () => {
    setName("");
    setAvatarFile(null);
    setAvatarPreview(null);
    setErrorField(null);
    setErrorMessage(null);
    setShowCredentials(false);
    setCredentials(null);
    setConnector("general");
    onClose();
  };

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setAvatarFile(file);
    setAvatarPreview(URL.createObjectURL(file));
  };

  const handleRecruit = async () => {
    setErrorField(null);
    setErrorMessage(null);

    if (!name.trim()) {
      setErrorField("name");
      setErrorMessage(t("people.recruit.nameRequired"));
      scrollToError("field-name");
      return;
    }

    setIsRecruiting(true);
    try {
      const res = await client("/api/users/agents", {
        method: "POST",
        json: { name: name.trim() },
      });

      if (avatarFile) {
        try {
          const formData = new FormData();
          formData.append("file", avatarFile);
          const token = localStorage.getItem("magnus_token");
          await fetch(`/api/users/${res.id}/avatar`, {
            method: "POST",
            headers: token ? { Authorization: `Bearer ${token}` } : {},
            body: formData,
          });
        } catch (err) {
          console.error("Avatar upload failed:", err);
        }
      }

      // Show credentials dialog instead of closing
      setCredentials({
        token: res.token,
        app_secret: res.app_secret,
      });
      setShowCredentials(true);
    } catch (e) {
      console.error(e);
    } finally {
      setIsRecruiting(false);
    }
  };

  return (
    <>
    <Drawer
      isOpen={isOpen}
      onClose={() => !isRecruiting && resetAndClose()}
      title={t("people.recruitTitle")}
      icon={<Plus className="w-5 h-5 text-blue-500" />}
      width="w-[440px]"
    >
      <div className="flex flex-col min-h-full">
        <div className="flex-1 space-y-6">
          {/* Avatar upload area */}
          <div className="flex justify-center">
            <div
              className="relative w-24 h-24 rounded-full bg-zinc-800 border-2 border-dashed border-zinc-700 flex items-center justify-center cursor-pointer group hover:border-blue-500/50 transition-colors overflow-hidden"
              onClick={() => fileInputRef.current?.click()}
            >
              {avatarPreview ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={avatarPreview} alt="Preview" className="w-full h-full object-cover" />
              ) : (
                <Camera className="w-8 h-8 text-zinc-600 group-hover:text-blue-500/60 transition-colors" />
              )}
              {avatarPreview && (
                <div className="absolute inset-0 bg-black/50 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity">
                  <Camera className="w-5 h-5 text-white" />
                </div>
              )}
              <input
                ref={fileInputRef}
                type="file"
                accept="image/png,image/jpeg,image/webp,image/gif"
                className="hidden"
                onChange={handleFileSelect}
              />
            </div>
          </div>
          <p className="text-center text-xs text-zinc-600">{t("people.drawer.avatarHint")}</p>

          {/* Name input */}
          <div id="field-name">
            <label className={`text-xs uppercase tracking-wider mb-1.5 block font-medium ${errorField === "name" ? "text-red-500" : "text-zinc-500"}`}>
              {t("people.recruit.name")} <span className="text-red-500">*</span>
            </label>
            <input
              value={name}
              onChange={(e) => { setName(e.target.value); clearError("name"); }}
              placeholder={t("people.recruit.namePlaceholder")}
              className={`w-full px-3 py-2 bg-zinc-900 border rounded-lg text-sm text-zinc-200 placeholder:text-zinc-600 focus:outline-none focus:border-blue-500/50 focus:ring-1 focus:ring-blue-500/20 ${errorField === "name" ? "animate-shake border-red-500" : "border-zinc-700"}`}
              autoFocus
              onKeyDown={(e) => { if (e.key === "Enter") handleRecruit(); }}
            />
          </div>

          {/* Connector selector */}
          <div>
            <label className="text-xs uppercase tracking-wider mb-1.5 block font-medium text-zinc-500">
              {t("people.recruit.connector")}
            </label>
            <div className="flex gap-2">
              {(["general", "openclaw"] as const).map((c) => (
                <button
                  key={c}
                  type="button"
                  onClick={() => setConnector(c)}
                  className={`px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors ${
                    connector === c
                      ? "bg-blue-600/20 border-blue-500/50 text-blue-400"
                      : "bg-zinc-900 border-zinc-700 text-zinc-400 hover:border-zinc-600"
                  }`}
                >
                  {c === "general" ? t("people.recruit.connectorGeneral") : t("people.recruit.connectorOpenClaw")}
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* Footer — always at bottom */}
        <div className="mt-auto pt-6 border-t border-zinc-800 flex flex-col-reverse sm:flex-row sm:justify-between sm:items-center gap-4">
          {errorMessage ? (
            <span className="text-red-500 text-xs font-bold animate-pulse">{errorMessage}</span>
          ) : (
            <span className="text-zinc-500 text-xs hidden sm:block" />
          )}
          <div className="flex gap-3 w-full sm:w-auto">
            <button
              onClick={resetAndClose}
              disabled={isRecruiting}
              className="flex-1 sm:flex-none px-4 py-2.5 rounded-lg text-sm font-medium text-zinc-400 hover:text-white hover:bg-zinc-800 transition-colors"
            >
              {t("common.cancel")}
            </button>
            <button
              onClick={handleRecruit}
              disabled={isRecruiting}
              className="flex-1 sm:flex-none px-6 py-2.5 rounded-lg text-sm font-medium bg-blue-600 hover:bg-blue-500 text-white shadow-lg shadow-blue-900/20 active:scale-95 transition-all flex items-center justify-center gap-2"
            >
              {isRecruiting && <Loader2 className="w-4 h-4 animate-spin" />}
              {t("people.recruit.submit")}
            </button>
          </div>
        </div>
      </div>
    </Drawer>

    {/* Credentials dialog — shown after successful agent creation */}
    {showCredentials && credentials && (
      <div className="fixed inset-0 z-[100] flex items-center justify-center p-4">
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm" />
        <div className="relative bg-[#09090b] border border-zinc-800 rounded-xl shadow-2xl w-full max-w-md overflow-hidden">
          <div className="p-6">
            <h3 className="text-base font-semibold text-zinc-100 mb-4">
              {t("people.recruit.credentialsTitle")}
            </h3>
            <div className="space-y-3">
              <div className="bg-zinc-900/50 rounded-lg border border-zinc-800/50 px-3 py-2">
                <span className="text-[10px] text-zinc-600 font-medium block mb-1">Token</span>
                <CopyableText text={credentials.token} variant="id" className="!text-zinc-300" />
              </div>
              <div className="bg-zinc-900/50 rounded-lg border border-zinc-800/50 px-3 py-2">
                <span className="text-[10px] text-zinc-600 font-medium block mb-1">App Secret</span>
                <CopyableText text={credentials.app_secret} variant="id" className="!text-zinc-300" />
              </div>
            </div>
            {connector === "openclaw" && (
              <div className="mt-4 pt-4 border-t border-zinc-800/50">
                <p className="text-xs text-zinc-500 mb-2">{t("people.recruit.openclawSetupHint")}</p>
                <div className="bg-zinc-900 rounded-lg border border-zinc-800 p-3 space-y-1.5">
                  {[
                    `openclaw config set channels.magnus.appSecret "${credentials.app_secret}"`,
                    `openclaw config set channels.magnus.magnusUrl "https://your-magnus-server"`,
                  ].map((cmd, i) => (
                    <CopyableText key={i} text={cmd} variant="id" className="!text-zinc-300 !text-xs font-mono" />
                  ))}
                </div>
              </div>
            )}

            <div className="mt-4 flex items-start gap-2 bg-amber-900/20 border border-amber-800/30 rounded-lg px-3 py-2">
              <AlertTriangle className="w-4 h-4 text-amber-500 flex-shrink-0 mt-0.5" />
              <p className="text-xs text-amber-400/80">{t("people.recruit.credentialsSaveWarning")}</p>
            </div>
          </div>
          <div className="bg-zinc-900/50 px-6 py-4 flex justify-end border-t border-zinc-800/50">
            <button
              onClick={() => {
                onSuccess();
                resetAndClose();
              }}
              className="px-4 py-2 rounded-lg text-sm font-medium text-white bg-blue-600 hover:bg-blue-500 border border-blue-500/50 shadow-lg transition-all"
            >
              {t("common.gotIt")}
            </button>
          </div>
        </div>
      </div>
    )}
    </>
  );
}
