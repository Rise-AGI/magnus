"use client";

import { ChangeEvent, type InputHTMLAttributes, useRef, useState } from "react";
import { Copy, Check, FolderUp, Upload, Loader2 } from "lucide-react";
import { useLanguage } from "@/context/language-context";
import { cn } from "@/lib/utils";
import { normalizeFileSecret, uploadDirectoryToSecret, uploadFileToSecret } from "@/lib/file-secret";

interface FileSecretInputProps {
  value: string;
  onChange: (val: string) => void;
  placeholder?: string;
  disabled?: boolean;
  hasError?: boolean;
}

const PREFIX = "magnus-secret:";

export function FileSecretInput({
  value,
  onChange,
  placeholder,
  disabled,
  hasError,
}: FileSecretInputProps) {
  const { t } = useLanguage();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const directoryInputRef = useRef<HTMLInputElement>(null);

  const [isUploading, setIsUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [uploadMode, setUploadMode] = useState<"file" | "directory" | null>(null);

  const secretValue = typeof value === "string" && value.startsWith(PREFIX)
    ? value.slice(PREFIX.length)
    : (value ?? "");

  const handleCopy = async () => {
    if (!value?.trim()) return;
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch {
      setUploadError(t("fileSecret.copyFailed"));
    }
  };

  const handleFileSelected = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;

    setIsUploading(true);
    setUploadMode("file");
    setUploadError(null);

    try {
      const result = await uploadFileToSecret(file);
      onChange(result.fileSecret);
    } catch (error) {
      const message = error instanceof Error ? error.message : t("fileSecret.uploadFailed");
      setUploadError(message);
    } finally {
      setIsUploading(false);
      setUploadMode(null);
      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }
    }
  };

  const handleDirectorySelected = async (event: ChangeEvent<HTMLInputElement>) => {
    const files = event.target.files ? Array.from(event.target.files) : [];
    if (!files.length) return;

    setIsUploading(true);
    setUploadMode("directory");
    setUploadError(null);

    try {
      const result = await uploadDirectoryToSecret(files);
      onChange(result.fileSecret);
    } catch (error) {
      const message = error instanceof Error ? error.message : t("fileSecret.uploadFailed");
      setUploadError(message);
    } finally {
      setIsUploading(false);
      setUploadMode(null);
      if (directoryInputRef.current) {
        directoryInputRef.current.value = "";
      }
    }
  };

  const directoryPickerProps = {
    webkitdirectory: "",
    directory: "",
    multiple: true,
  } as unknown as InputHTMLAttributes<HTMLInputElement>;

  return (
    <div className="space-y-2">
      <div className="flex flex-col gap-2">
        <div className="flex min-w-0 items-center">
          <span className="select-none rounded-l-lg border border-r-0 border-zinc-800 bg-zinc-900 px-3 py-2.5 text-sm text-zinc-500">
            {PREFIX}
          </span>
          <input
            type="text"
            value={secretValue}
            onChange={(e) => onChange(normalizeFileSecret(e.target.value))}
            placeholder={placeholder || "secret-code"}
            className={cn(
              "flex-1 min-w-0 bg-zinc-950 border px-3 py-2.5 text-sm transition-all outline-none placeholder-zinc-700",
              "focus:border-blue-500 focus:ring-1 focus:ring-blue-500/20",
              secretValue ? "rounded-none border-zinc-800" : "rounded-r-lg border-zinc-800",
              disabled && "opacity-40 cursor-not-allowed",
              hasError && "border-red-500 animate-shake"
            )}
            spellCheck={false}
            disabled={disabled || isUploading}
          />
          {secretValue && (
            <button
              type="button"
              onClick={handleCopy}
              disabled={disabled || isUploading}
              className="px-3 border border-l-0 border-zinc-800 rounded-r-lg bg-zinc-950 text-zinc-400 hover:text-white hover:bg-zinc-900 transition-colors disabled:opacity-40"
              title={copied ? t("action.copied") : t("action.copy")}
            >
              {copied ? <Check className="w-4 h-4" /> : <Copy className="w-4 h-4" />}
            </button>
          )}
        </div>

        <div className="flex flex-wrap gap-2">
          <input
            ref={fileInputRef}
            type="file"
            className="hidden"
            onChange={handleFileSelected}
            disabled={disabled || isUploading}
          />

          <input
            ref={directoryInputRef}
            type="file"
            className="hidden"
            onChange={handleDirectorySelected}
            disabled={disabled || isUploading}
            {...directoryPickerProps}
          />

          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            disabled={disabled || isUploading}
            className="inline-flex shrink-0 items-center gap-2 rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-300 transition-colors hover:border-zinc-700 hover:bg-zinc-800 hover:text-white disabled:cursor-not-allowed disabled:opacity-40"
          >
            {isUploading && uploadMode === "file" ? <Loader2 className="w-4 h-4 animate-spin" /> : <Upload className="w-4 h-4" />}
            <span className="font-medium">{isUploading && uploadMode === "file" ? t("fileSecret.uploading") : t("fileSecret.upload")}</span>
          </button>

          <button
            type="button"
            onClick={() => directoryInputRef.current?.click()}
            disabled={disabled || isUploading}
            className="inline-flex shrink-0 items-center gap-2 rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-300 transition-colors hover:border-zinc-700 hover:bg-zinc-800 hover:text-white disabled:cursor-not-allowed disabled:opacity-40"
          >
            {isUploading && uploadMode === "directory" ? <Loader2 className="w-4 h-4 animate-spin" /> : <FolderUp className="w-4 h-4" />}
            <span className="font-medium">
              {isUploading && uploadMode === "directory" ? t("fileSecret.packing") : t("fileSecret.uploadFolder")}
            </span>
          </button>
        </div>
      </div>

      <div className="space-y-1 text-xs">
        <p className="text-zinc-500">
          {t("fileSecret.webHint")}
        </p>
        {uploadError && (
          <p className="text-red-400">
            {uploadError}
          </p>
        )}
      </div>
    </div>
  );
}
