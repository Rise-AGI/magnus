// front_end/src/components/people/recruit-drawer.tsx
"use client";

import { useState, useRef } from "react";
import { Plus, Camera, Loader2 } from "lucide-react";
import { client } from "@/lib/api";
import { Drawer } from "@/components/ui/drawer";
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

  const resetAndClose = () => {
    setName("");
    setAvatarFile(null);
    setAvatarPreview(null);
    onClose();
  };

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setAvatarFile(file);
    setAvatarPreview(URL.createObjectURL(file));
  };

  const handleRecruit = async () => {
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

      onSuccess();
      resetAndClose();
    } catch (e) {
      console.error(e);
    } finally {
      setIsRecruiting(false);
    }
  };

  return (
    <Drawer
      isOpen={isOpen}
      onClose={() => !isRecruiting && resetAndClose()}
      title={t("people.recruitTitle")}
      icon={<Plus className="w-5 h-5 text-blue-500" />}
      width="w-[440px]"
    >
      <div className="space-y-6">
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
        <div>
          <label className="text-xs text-zinc-500 font-medium block mb-1">{t("people.recruit.name")}</label>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={t("people.recruit.namePlaceholder")}
            className="w-full px-3 py-2 bg-zinc-900 border border-zinc-700 rounded-lg text-sm text-zinc-200 placeholder:text-zinc-600 focus:outline-none focus:border-blue-500/50 focus:ring-1 focus:ring-blue-500/20"
            autoFocus
            onKeyDown={(e) => { if (e.key === "Enter" && name.trim()) handleRecruit(); }}
          />
        </div>

        {/* Submit */}
        <div className="flex justify-end gap-3">
          <button
            onClick={resetAndClose}
            disabled={isRecruiting}
            className="px-4 py-2 rounded-lg text-sm font-medium text-zinc-400 hover:text-white hover:bg-zinc-800 transition-colors"
          >
            {t("common.cancel")}
          </button>
          <button
            onClick={handleRecruit}
            disabled={isRecruiting || !name.trim()}
            className="px-6 py-2 rounded-lg text-sm font-medium bg-blue-600 hover:bg-blue-500 text-white shadow-lg shadow-blue-900/20 active:scale-95 transition-all flex items-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {isRecruiting && <Loader2 className="w-4 h-4 animate-spin" />}
            {t("people.recruit.submit")}
          </button>
        </div>
      </div>
    </Drawer>
  );
}
