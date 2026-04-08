"use client";

import { ChangeEvent, type InputHTMLAttributes, useEffect, useRef, useState } from "react";
import {
  AlertCircle,
  Check,
  Clock3,
  Copy,
  Download,
  FileUp,
  FolderUp,
  History,
  Loader2,
  Trash2,
} from "lucide-react";
import { useLanguage } from "@/context/language-context";
import {
  downloadFileSecret,
  formatFileSize,
  normalizeFileSecret,
  uploadDirectoryToSecret,
  uploadFileToSecret,
} from "@/lib/file-secret";
import { NumberStepper } from "@/components/ui/number-stepper";
import { CopyableText } from "@/components/ui/copyable-text";

interface RecentSecretEntry {
  secret: string;
  fileName: string;
  sourceName?: string;
  size?: number;
  direction: "upload" | "download";
  kind?: "file" | "directory";
  timestamp: number;
}

const HISTORY_KEY = "magnus_recent_file_secrets";
const DEFAULT_EXPIRE_MINUTES = 60;
const DEFAULT_MAX_DOWNLOADS = 1;
const MAX_HISTORY_ITEMS = 8;

function formatRelativeTime(timestamp: number, language: "zh" | "en") {
  const diffMs = Date.now() - timestamp;
  const diffMinutes = Math.max(0, Math.round(diffMs / 60000));
  if (diffMinutes < 1) return language === "zh" ? "刚刚" : "just now";
  if (diffMinutes < 60) return language === "zh" ? `${diffMinutes} 分钟前` : `${diffMinutes} min ago`;
  const diffHours = Math.round(diffMinutes / 60);
  if (diffHours < 24) return language === "zh" ? `${diffHours} 小时前` : `${diffHours} hr ago`;
  const diffDays = Math.round(diffHours / 24);
  if (language === "zh") return `${diffDays} 天前`;
  return `${diffDays} day${diffDays > 1 ? "s" : ""} ago`;
}

function RecentSecretActions({
  entry,
  onCopy,
  onDownload,
  onRemove,
  isBusy,
  copiedSecret,
  t,
}: {
  entry: RecentSecretEntry;
  onCopy: (secret: string) => Promise<void>;
  onDownload: (secret: string) => Promise<void>;
  onRemove: (secret: string) => void;
  isBusy: boolean;
  copiedSecret: string | null;
  t: ReturnType<typeof useLanguage>["t"];
}) {
  return (
    <div className="flex items-center gap-2">
      <button
        type="button"
        onClick={() => void onCopy(entry.secret)}
        className="inline-flex items-center gap-1 rounded-md border border-zinc-800 bg-zinc-950 px-2.5 py-1.5 text-xs text-zinc-300 transition-colors hover:border-zinc-700 hover:text-white"
      >
        {copiedSecret === entry.secret ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
        <span className="hidden sm:inline">{copiedSecret === entry.secret ? t("action.copied") : t("action.copy")}</span>
      </button>
      <button
        type="button"
        onClick={() => void onDownload(entry.secret)}
        disabled={isBusy}
        className="inline-flex items-center gap-1 rounded-md border border-zinc-800 bg-zinc-950 px-2.5 py-1.5 text-xs text-zinc-300 transition-colors hover:border-zinc-700 hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
      >
        <Download className="h-3.5 w-3.5" />
        <span className="hidden sm:inline">{t("files.downloadButton")}</span>
      </button>
      <button
        type="button"
        onClick={() => onRemove(entry.secret)}
        className="inline-flex items-center gap-1 rounded-md border border-zinc-800 bg-zinc-950 px-2.5 py-1.5 text-xs text-zinc-500 transition-colors hover:border-red-900/60 hover:bg-red-950/30 hover:text-red-300"
      >
        <Trash2 className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}

export default function ToolsPage() {
  const { t, language } = useLanguage();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const directoryInputRef = useRef<HTMLInputElement>(null);

  const [downloadSecret, setDownloadSecret] = useState("");
  const [expireMinutes, setExpireMinutes] = useState(DEFAULT_EXPIRE_MINUTES);
  const [maxDownloads, setMaxDownloads] = useState(DEFAULT_MAX_DOWNLOADS);
  const [recentSecrets, setRecentSecrets] = useState<RecentSecretEntry[]>([]);
  const [copiedSecret, setCopiedSecret] = useState<string | null>(null);

  const [uploading, setUploading] = useState(false);
  const [uploadMode, setUploadMode] = useState<"file" | "directory" | null>(null);
  const [downloading, setDownloading] = useState(false);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [latestUpload, setLatestUpload] = useState<RecentSecretEntry | null>(null);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const raw = localStorage.getItem(HISTORY_KEY);
    if (!raw) return;

    try {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed)) {
        setRecentSecrets(
          parsed.filter((entry): entry is RecentSecretEntry => {
            return Boolean(entry?.secret && entry?.fileName && entry?.direction && entry?.timestamp);
          }),
        );
      }
    } catch {
      localStorage.removeItem(HISTORY_KEY);
    }
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") return;
    localStorage.setItem(HISTORY_KEY, JSON.stringify(recentSecrets));
  }, [recentSecrets]);

  const pushRecent = (entry: RecentSecretEntry) => {
    setRecentSecrets((current) => {
      const deduped = current.filter((item) => item.secret !== entry.secret);
      return [entry, ...deduped].slice(0, MAX_HISTORY_ITEMS);
    });
  };

  const flashCopied = (secret: string) => {
    setCopiedSecret(secret);
    window.setTimeout(() => setCopiedSecret((current) => (current === secret ? null : current)), 1200);
  };

  const handleCopy = async (secret: string) => {
    try {
      await navigator.clipboard.writeText(secret);
      flashCopied(secret);
      setErrorMessage(null);
    } catch {
      setErrorMessage(t("fileSecret.copyFailed"));
    }
  };

  const handleUpload = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;

    setUploading(true);
    setUploadMode("file");
    setErrorMessage(null);
    setStatusMessage(null);

    try {
      const result = await uploadFileToSecret(file, { expireMinutes, maxDownloads });
      const entry: RecentSecretEntry = {
        secret: result.fileSecret,
        fileName: result.fileName,
        sourceName: result.sourceName,
        size: result.size,
        direction: "upload",
        kind: result.kind,
        timestamp: Date.now(),
      };

      setLatestUpload(entry);
      setDownloadSecret(result.fileSecret);
      setStatusMessage(t("files.uploadSuccess", { name: result.sourceName }));
      pushRecent(entry);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : t("fileSecret.uploadFailed"));
    } finally {
      setUploading(false);
      setUploadMode(null);
      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }
    }
  };

  const handleDirectoryUpload = async (event: ChangeEvent<HTMLInputElement>) => {
    const files = event.target.files ? Array.from(event.target.files) : [];
    if (!files.length) return;

    setUploading(true);
    setUploadMode("directory");
    setErrorMessage(null);
    setStatusMessage(null);

    try {
      const result = await uploadDirectoryToSecret(files, { expireMinutes, maxDownloads });
      const entry: RecentSecretEntry = {
        secret: result.fileSecret,
        fileName: result.fileName,
        sourceName: result.sourceName,
        size: result.size,
        direction: "upload",
        kind: result.kind,
        timestamp: Date.now(),
      };

      setLatestUpload(entry);
      setDownloadSecret(result.fileSecret);
      setStatusMessage(t("files.uploadSuccess", { name: result.sourceName }));
      pushRecent(entry);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : t("fileSecret.uploadFailed"));
    } finally {
      setUploading(false);
      setUploadMode(null);
      if (directoryInputRef.current) {
        directoryInputRef.current.value = "";
      }
    }
  };

  const handleDownload = async (secret: string) => {
    const normalized = normalizeFileSecret(secret);
    if (!normalized) {
      setErrorMessage(t("files.secretRequired"));
      return;
    }

    setDownloading(true);
    setErrorMessage(null);
    setStatusMessage(null);

    try {
      const result = await downloadFileSecret(normalized);
      const entry: RecentSecretEntry = {
        secret: normalized,
        fileName: result.fileName,
        size: result.size,
        direction: "download",
        timestamp: Date.now(),
      };

      setDownloadSecret(normalized);
      setStatusMessage(t("files.downloadSuccess", { name: result.fileName }));
      pushRecent(entry);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : t("files.downloadFailed"));
    } finally {
      setDownloading(false);
    }
  };

  const removeRecent = (secret: string) => {
    setRecentSecrets((current) => current.filter((entry) => entry.secret !== secret));
  };

  const clearRecent = () => {
    setRecentSecrets([]);
    if (typeof window !== "undefined") {
      localStorage.removeItem(HISTORY_KEY);
    }
  };

  const directoryPickerProps = {
    webkitdirectory: "",
    directory: "",
    multiple: true,
  } as unknown as InputHTMLAttributes<HTMLInputElement>;

  return (
    <div className="relative min-h-[calc(100vh-8rem)] pb-20">
      <style jsx global>{`
        ::-webkit-scrollbar { display: none; }
        html { -ms-overflow-style: none; scrollbar-width: none; }
      `}</style>

      <div className="flex flex-col sm:flex-row items-start sm:items-center sm:justify-between gap-4 mb-8">
        <div>
          <h1 className="text-2xl font-bold text-white tracking-tight">{t("nav.tools")}</h1>
          <p className="text-zinc-500 text-sm mt-1">{t("files.subtitle")}</p>
        </div>
      </div>

      <div className="bg-zinc-900/40 border border-zinc-800 rounded-xl px-4 py-3 mb-6 flex items-start gap-3 backdrop-blur-sm">
        <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-zinc-400" />
        <div className="space-y-1 text-sm text-zinc-400">
          <p>{t("files.warning")}</p>
          <p>{t("files.tip")}</p>
        </div>
      </div>

      <div className="grid gap-6 xl:grid-cols-2">
        <section className="bg-zinc-900/40 border border-zinc-800 rounded-xl p-6 backdrop-blur-sm">
          <div className="mb-6 flex items-center gap-3">
            <div className="rounded-lg border border-zinc-800 bg-zinc-950 p-2 text-zinc-300">
              <FileUp className="h-5 w-5" />
            </div>
            <div>
              <h2 className="text-lg font-semibold text-white">{t("files.uploadTitle")}</h2>
              <p className="text-sm text-zinc-500 mt-1">{t("files.uploadDescription")}</p>
            </div>
          </div>

          <div className="rounded-xl border border-zinc-800 bg-zinc-950/40 p-4">
            <div className="mb-4">
              <div>
                <p className="text-sm font-medium text-zinc-100">{t("files.uploadBoxTitle")}</p>
                <p className="mt-1 text-sm text-zinc-500">{t("files.folderPackaging")}</p>
              </div>
            </div>

            <input
              ref={fileInputRef}
              type="file"
              className="hidden"
              onChange={handleUpload}
              disabled={uploading}
            />

            <input
              ref={directoryInputRef}
              type="file"
              className="hidden"
              onChange={handleDirectoryUpload}
              disabled={uploading}
              {...directoryPickerProps}
            />

            <div className="grid gap-3 sm:grid-cols-2">
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                disabled={uploading}
                className="flex w-full items-center justify-center gap-2 rounded-lg border border-zinc-800 bg-zinc-950 px-4 py-3 text-sm font-medium text-zinc-200 transition-colors hover:border-zinc-700 hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
              >
                {uploading && uploadMode === "file" ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileUp className="h-4 w-4" />}
                <span>{uploading && uploadMode === "file" ? t("fileSecret.uploading") : t("files.selectFile")}</span>
              </button>

              <button
                type="button"
                onClick={() => directoryInputRef.current?.click()}
                disabled={uploading}
                className="flex w-full items-center justify-center gap-2 rounded-lg border border-zinc-800 bg-zinc-950 px-4 py-3 text-sm font-medium text-zinc-200 transition-colors hover:border-zinc-700 hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
              >
                {uploading && uploadMode === "directory" ? <Loader2 className="h-4 w-4 animate-spin" /> : <FolderUp className="h-4 w-4" />}
                <span>{uploading && uploadMode === "directory" ? t("fileSecret.packing") : t("files.selectFolder")}</span>
              </button>
            </div>

            <div className="mt-3 text-sm text-zinc-500 space-y-1">
              <p>{t("files.dragHint")}</p>
              <p>{t("files.folderHint")}</p>
              <p>{t("files.folderRuntimeHint")}</p>
            </div>
          </div>

          <div className="grid gap-4 bg-zinc-900/40 border border-zinc-800 rounded-xl p-4 sm:grid-cols-2 mt-5">
            <NumberStepper
              label={t("files.expireMinutes")}
              value={expireMinutes}
              onChange={setExpireMinutes}
              min={1}
              max={1440}
            />
            <NumberStepper
              label={t("files.maxDownloads")}
              value={maxDownloads}
              onChange={setMaxDownloads}
              min={1}
              max={100}
            />
          </div>

          {latestUpload && (
            <div className="mt-5 rounded-xl border border-zinc-800 bg-zinc-950/40 p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <p className="text-sm font-medium text-zinc-100">{t("files.readyTitle")}</p>
                  <p className="mt-1 text-sm text-zinc-300">{latestUpload.sourceName || latestUpload.fileName}</p>
                  <p className="mt-1 text-xs text-zinc-500">
                    {latestUpload.kind === "directory"
                      ? `${t("files.folderTag")} · ${latestUpload.fileName} · ${formatFileSize(latestUpload.size || 0)}`
                      : formatFileSize(latestUpload.size || 0)}
                  </p>
                </div>
                <div className="flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={() => void handleCopy(latestUpload.secret)}
                    className="inline-flex items-center gap-2 rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-1.5 text-xs font-medium text-zinc-200 transition hover:border-zinc-700 hover:text-white"
                  >
                    {copiedSecret === latestUpload.secret ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
                    {copiedSecret === latestUpload.secret ? t("action.copied") : t("action.copy")}
                  </button>
                  <button
                    type="button"
                    onClick={() => setDownloadSecret(latestUpload.secret)}
                    className="inline-flex items-center gap-2 rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-1.5 text-xs font-medium text-zinc-200 transition hover:border-zinc-700 hover:text-white"
                  >
                    <Download className="h-3.5 w-3.5" />
                    {t("files.useForDownload")}
                  </button>
                </div>
              </div>
              <div className="mt-3 rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2">
                <CopyableText text={latestUpload.secret} variant="text" className="font-mono text-xs text-zinc-200 break-all" />
              </div>
              <p className="mt-3 text-xs leading-5 text-zinc-500">{t("files.readyHint")}</p>
            </div>
          )}
        </section>

        <section className="bg-zinc-900/40 border border-zinc-800 rounded-xl p-6 backdrop-blur-sm">
          <div className="mb-6 flex items-center gap-3">
            <div className="rounded-lg border border-zinc-800 bg-zinc-950 p-2 text-zinc-300">
              <Download className="h-5 w-5" />
            </div>
            <div>
              <h2 className="text-lg font-semibold text-white">{t("files.downloadTitle")}</h2>
              <p className="text-sm text-zinc-500 mt-1">{t("files.downloadDescription")}</p>
            </div>
          </div>

          <div className="rounded-xl border border-zinc-800 bg-zinc-950/40 p-4">
            <div className="mb-3 flex items-center gap-2 text-xs uppercase tracking-[0.2em] text-zinc-500">
              <Clock3 className="h-3.5 w-3.5" />
              {t("files.downloadFlow")}
            </div>
            <p className="mb-4 text-sm text-zinc-500">{t("files.downloadHint")}</p>

            <div className="space-y-2">
              <span className="text-xs font-medium uppercase tracking-[0.2em] text-zinc-500">
                {t("files.secretLabel")}
              </span>
              <div className="flex items-center">
                <span className="select-none rounded-l-lg border border-r-0 border-zinc-800 bg-zinc-900 px-3 py-2.5 text-sm text-zinc-500">
                  magnus-secret:
                </span>
                <input
                  type="text"
                  value={downloadSecret.startsWith("magnus-secret:") ? downloadSecret.slice(14) : downloadSecret}
                  onChange={(e) => setDownloadSecret(normalizeFileSecret(e.target.value))}
                  placeholder="..."
                  className="flex-1 min-w-0 rounded-r-lg border border-zinc-800 bg-zinc-950 px-3 py-2.5 text-sm text-zinc-100 outline-none transition focus:border-blue-500 focus:ring-1 focus:ring-blue-500/20"
                  spellCheck={false}
                />
              </div>
            </div>

            <div className="mt-5 flex flex-wrap gap-3">
              <button
                type="button"
                onClick={() => void handleDownload(downloadSecret)}
                disabled={downloading}
                className="bg-blue-600 hover:bg-blue-500 text-white px-4 py-2 rounded-lg text-sm font-medium inline-flex items-center gap-2 transition-colors shadow-lg shadow-blue-900/20 active:scale-95 border border-blue-500/50 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {downloading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
                {downloading ? t("files.downloading") : t("files.downloadButton")}
              </button>
              <button
                type="button"
                onClick={() => void handleCopy(downloadSecret)}
                disabled={!downloadSecret.trim()}
                className="inline-flex items-center gap-2 rounded-lg border border-zinc-800 bg-zinc-950 px-4 py-2.5 text-sm font-medium text-zinc-200 transition hover:border-zinc-700 hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
              >
                {copiedSecret === downloadSecret ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
                {copiedSecret === downloadSecret ? t("action.copied") : t("action.copy")}
              </button>
            </div>
          </div>

          {(statusMessage || errorMessage) && (
            <div className={`mt-5 rounded-xl border px-4 py-3 text-sm ${errorMessage ? "border-red-900/50 bg-red-950/30 text-red-200" : "border-zinc-800 bg-zinc-950/40 text-zinc-300"}`}>
              {errorMessage || statusMessage}
            </div>
          )}
        </section>
      </div>

      <section className="mt-6 bg-zinc-900/40 border border-zinc-800 rounded-xl p-6 backdrop-blur-sm">
        <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="rounded-lg border border-zinc-800 bg-zinc-950 p-2 text-zinc-300">
              <History className="h-5 w-5" />
            </div>
            <div>
              <h2 className="text-lg font-semibold text-white">{t("files.recentTitle")}</h2>
              <p className="text-sm text-zinc-500">{t("files.recentDescription")}</p>
            </div>
          </div>
          {recentSecrets.length > 0 && (
            <button
              type="button"
              onClick={clearRecent}
              className="inline-flex items-center gap-2 rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-xs font-medium text-zinc-400 transition hover:border-zinc-700 hover:text-white"
            >
              <Trash2 className="h-3.5 w-3.5" />
              {t("files.clearRecent")}
            </button>
          )}
        </div>

        {recentSecrets.length === 0 ? (
          <div className="rounded-xl border border-dashed border-zinc-800 bg-zinc-950/40 px-4 py-8 text-center text-sm text-zinc-500">
            {t("files.noRecent")}
          </div>
        ) : (
          <div className="space-y-3">
            {recentSecrets.map((entry) => (
              <div
                key={`${entry.secret}-${entry.timestamp}`}
                className="flex flex-col gap-3 rounded-2xl border border-zinc-800 bg-zinc-950/50 px-4 py-3 md:flex-row md:items-center md:justify-between"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="truncate text-sm font-medium text-zinc-100">{entry.fileName}</p>
                    <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${entry.direction === "upload" ? "bg-blue-500/10 text-blue-300" : "bg-emerald-500/10 text-emerald-300"}`}>
                      {entry.direction === "upload" ? t("files.uploadTag") : t("files.downloadTag")}
                    </span>
                    {entry.kind === "directory" && (
                      <span className="rounded-full bg-cyan-500/10 px-2 py-0.5 text-[11px] font-medium text-cyan-300">
                        {t("files.folderTag")}
                      </span>
                    )}
                    {typeof entry.size === "number" && (
                      <span className="text-xs text-zinc-500">{formatFileSize(entry.size)}</span>
                    )}
                    <span className="text-xs text-zinc-600">{formatRelativeTime(entry.timestamp, language)}</span>
                  </div>
                  <div className="mt-1 truncate text-xs text-zinc-500">
                    {entry.sourceName && entry.sourceName !== entry.fileName ? `${entry.sourceName} -> ${entry.fileName}` : entry.fileName}
                  </div>
                  <CopyableText text={entry.secret} variant="id" className="mt-1" />
                </div>
                <RecentSecretActions
                  entry={entry}
                  onCopy={handleCopy}
                  onDownload={handleDownload}
                  onRemove={removeRecent}
                  isBusy={downloading}
                  copiedSecret={copiedSecret}
                  t={t}
                />
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
