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
import { client } from "@/lib/api";
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
  const [sharedExpireDays, setSharedExpireDays] = useState(7);
  const [sharedExpectedSizeGb, setSharedExpectedSizeGb] = useState(10);
  const [creatingShared, setCreatingShared] = useState(false);
  const [createdSharedToken, setCreatedSharedToken] = useState<string | null>(null);
  
  // Shared folder management state
  const [manageToken, setManageToken] = useState("");
  const [sharedInfo, setSharedInfo] = useState<Record<string, any> | null>(null);
  const [sharedFiles, setSharedFiles] = useState<Array<Record<string, any>>>([]);
  const [sharedPath, setSharedPath] = useState("");
  const [loadingShared, setLoadingShared] = useState(false);
  const [extendDays, setExtendDays] = useState(7);
  const [newExpectedSize, setNewExpectedSize] = useState(10);
  const [restoreDays, setRestoreDays] = useState(7);

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

  const handleCreateSharedFolder = async () => {
    setCreatingShared(true);
    setErrorMessage(null);
    try {
      const result = await client("/api/shared-files", {
        method: "POST",
        json: {
          expire_days: sharedExpireDays,
          expected_size_gb: sharedExpectedSizeGb,
        },
      });
      setCreatedSharedToken(result.token || "");
      setStatusMessage(t("files.sharedCreateSuccess"));
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : t("files.sharedCreateFailed"));
    } finally {
      setCreatingShared(false);
    }
  };

  // Shared folder management functions
  const handleViewSharedFolder = async () => {
    if (!manageToken.trim()) return;
    setLoadingShared(true);
    setErrorMessage(null);
    setSharedInfo(null);
    setSharedFiles([]);
    setSharedPath("");
    try {
      const info = await client(`/api/shared-files/${manageToken.trim()}`);
      setSharedInfo(info);
      if (info.status === "active") {
        await loadSharedFiles("");
      }
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : t("files.sharedNotFound"));
    } finally {
      setLoadingShared(false);
    }
  };

  const loadSharedFiles = async (subpath: string) => {
    if (!manageToken.trim()) return;
    try {
      const result = await client(`/api/shared-files/${manageToken.trim()}/files?path=${encodeURIComponent(subpath)}`);
      setSharedFiles(result.files || []);
      setSharedPath(subpath);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : t("files.sharedNotFound"));
    }
  };

  const handleDownloadSharedFile = async (filePath: string, fileName: string) => {
    try {
      const response = await fetch(
        `/api/shared-files/${manageToken.trim()}/download?path=${encodeURIComponent(filePath)}`,
        { headers: { Authorization: `Bearer ${localStorage.getItem("token")}` } }
      );
      if (!response.ok) throw new Error("Download failed");
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = fileName;
      a.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : t("files.downloadFailed"));
    }
  };

  const handleUpdateSharedFolder = async () => {
    if (!manageToken.trim()) return;
    setLoadingShared(true);
    setErrorMessage(null);
    try {
      await client(`/api/shared-files/${manageToken.trim()}`, {
        method: "PATCH",
        json: {
          expected_size_gb: newExpectedSize || undefined,
          extend_days: extendDays || undefined,
        },
      });
      setStatusMessage(t("files.sharedUpdateSuccess"));
      await handleViewSharedFolder();
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : t("files.sharedUpdateFailed"));
    } finally {
      setLoadingShared(false);
    }
  };

  const handleRestoreSharedFolder = async () => {
    if (!manageToken.trim()) return;
    setLoadingShared(true);
    setErrorMessage(null);
    try {
      await client(`/api/shared-files/${manageToken.trim()}/restore`, {
        method: "POST",
        json: { new_expire_days: restoreDays },
      });
      setStatusMessage(t("files.sharedRestoreSuccess"));
      await handleViewSharedFolder();
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : t("files.sharedRestoreFailed"));
    } finally {
      setLoadingShared(false);
    }
  };

  const formatBytes = (bytes: number) => {
    if (bytes === 0) return "0 B";
    const k = 1024;
    const sizes = ["B", "KB", "MB", "GB", "TB"];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + " " + sizes[i];
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

      <section className="bg-zinc-900/40 border border-zinc-800 rounded-xl p-6 mb-6 backdrop-blur-sm">
        <h2 className="text-lg font-semibold text-white">{t("files.sharedTitle")}</h2>
        <p className="text-sm text-zinc-500 mt-1">{t("files.sharedDescription")}</p>
        <div className="grid gap-4 sm:grid-cols-2 mt-4">
          <NumberStepper
            label={t("files.sharedExpireDays")}
            value={sharedExpireDays}
            onChange={setSharedExpireDays}
            min={7}
            max={90}
          />
          <NumberStepper
            label={t("files.sharedExpectedSize")}
            value={sharedExpectedSizeGb}
            onChange={setSharedExpectedSizeGb}
            min={1}
            max={800}
          />
        </div>
        <button
          type="button"
          onClick={() => void handleCreateSharedFolder()}
          disabled={creatingShared}
          className="mt-4 bg-blue-600 hover:bg-blue-500 text-white px-4 py-2 rounded-lg text-sm font-medium inline-flex items-center gap-2 transition-colors shadow-lg shadow-blue-900/20 active:scale-95 border border-blue-500/50 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {creatingShared ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
          {t("files.sharedCreate")}
        </button>
      </section>

      {/* Shared Folder Management */}
      <section className="bg-zinc-900/40 border border-zinc-800 rounded-xl p-6 backdrop-blur-sm">
        <h2 className="text-lg font-semibold text-white">{t("files.sharedManageTitle")}</h2>
        <p className="text-sm text-zinc-500 mt-1">{t("files.sharedManageHint")}</p>
        <div className="mt-4 flex gap-2">
          <input
            type="text"
            value={manageToken}
            onChange={(e) => setManageToken(e.target.value)}
            placeholder={t("files.sharedTokenLabel")}
            className="flex-1 rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 outline-none transition focus:border-blue-500"
          />
          <button
            type="button"
            onClick={() => void handleViewSharedFolder()}
            disabled={loadingShared || !manageToken.trim()}
            className="bg-blue-600 hover:bg-blue-500 text-white px-4 py-2 rounded-lg text-sm font-medium inline-flex items-center gap-2 transition-colors disabled:cursor-not-allowed disabled:opacity-50"
          >
            {loadingShared ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
            {t("files.sharedViewButton")}
          </button>
        </div>

        {sharedInfo && (
          <div className="mt-4 space-y-4">
            {/* Info */}
            <div className="rounded-lg border border-zinc-800 bg-zinc-950/50 p-4">
              <div className="flex items-center gap-2 mb-3">
                <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${sharedInfo.status === "active" ? "bg-green-500/20 text-green-300" : "bg-yellow-500/20 text-yellow-300"}`}>
                  {sharedInfo.status === "active" ? t("files.sharedActive") : t("files.sharedArchived")}
                </span>
              </div>
              {sharedInfo.created_at && (
                <div className="grid grid-cols-2 gap-2 text-sm">
                  <div className="text-zinc-500">{t("files.sharedCreatedAt")}:</div>
                  <div className="text-zinc-300">{new Date(sharedInfo.created_at).toLocaleString()}</div>
                  {sharedInfo.expire_at && (
                    <>
                      <div className="text-zinc-500">{t("files.sharedExpiresAt")}:</div>
                      <div className="text-zinc-300">{new Date(sharedInfo.expire_at).toLocaleString()}</div>
                    </>
                  )}
                  {sharedInfo.actual_size_bytes !== undefined && (
                    <>
                      <div className="text-zinc-500">{t("files.sharedActualSize")}:</div>
                      <div className="text-zinc-300">{formatBytes(sharedInfo.actual_size_bytes)}</div>
                    </>
                  )}
                  {sharedInfo.expected_size_gb && (
                    <>
                      <div className="text-zinc-500">{t("files.sharedExpectedSize")}:</div>
                      <div className="text-zinc-300">{sharedInfo.expected_size_gb} GB</div>
                    </>
                  )}
                </div>
              )}
            </div>

            {/* Files browser (active only) */}
            {sharedInfo.status === "active" && (
              <div className="rounded-lg border border-zinc-800 bg-zinc-950/50 p-4">
                <div className="flex items-center justify-between mb-3">
                  <h3 className="text-sm font-medium text-zinc-200">{t("files.sharedFiles")}</h3>
                  {sharedPath && (
                    <button
                      type="button"
                      onClick={() => loadSharedFiles("")}
                      className="text-xs text-blue-400 hover:text-blue-300"
                    >
                      {t("files.sharedBackToRoot")}
                    </button>
                  )}
                </div>
                {sharedPath && (
                  <div className="text-xs text-zinc-500 mb-2">
                    {t("files.sharedCurrentPath")}: {sharedPath}
                  </div>
                )}
                {sharedFiles.length === 0 ? (
                  <div className="text-sm text-zinc-500">{t("files.sharedNoFiles")}</div>
                ) : (
                  <div className="space-y-1">
                    {sharedFiles.map((file) => (
                      <div
                        key={file.path}
                        className="flex items-center justify-between py-1.5 px-2 rounded hover:bg-zinc-800/50"
                      >
                        <div
                          className="flex items-center gap-2 cursor-pointer flex-1"
                          onClick={() => file.type === "directory" && loadSharedFiles(file.path)}
                        >
                          <span className="text-zinc-300">{file.name}</span>
                          {file.type === "directory" && (
                            <span className="text-xs text-zinc-500">/</span>
                          )}
                          {file.size !== undefined && (
                            <span className="text-xs text-zinc-500">({formatBytes(file.size)})</span>
                          )}
                        </div>
                        {file.type === "file" && (
                          <button
                            type="button"
                            onClick={() => handleDownloadSharedFile(file.path, file.name)}
                            className="text-xs text-blue-400 hover:text-blue-300"
                          >
                            {t("files.sharedDownloadFile")}
                          </button>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* Update / Restore (creator/admin only) */}
            {(sharedInfo.is_creator || sharedInfo.is_admin) && (
              <div className="rounded-lg border border-zinc-800 bg-zinc-950/50 p-4">
                {sharedInfo.status === "archived" ? (
                  <div className="space-y-3">
                    <p className="text-sm text-zinc-400">{t("files.sharedRestoreHint")}</p>
                    <div className="flex items-end gap-3">
                      <div className="flex-1">
                        <label className="text-xs text-zinc-500">{t("files.sharedRestoreDays")}</label>
                        <input
                          type="number"
                          value={restoreDays}
                          onChange={(e) => setRestoreDays(parseInt(e.target.value) || 7)}
                          min={7}
                          max={90}
                          className="w-full mt-1 rounded border border-zinc-800 bg-zinc-950 px-2 py-1.5 text-sm text-zinc-100"
                        />
                      </div>
                      <button
                        type="button"
                        onClick={() => handleRestoreSharedFolder()}
                        disabled={loadingShared}
                        className="bg-green-600 hover:bg-green-500 text-white px-4 py-1.5 rounded text-sm font-medium"
                      >
                        {t("files.sharedRestoreButton")}
                      </button>
                    </div>
                  </div>
                ) : (
                  <div className="space-y-3">
                    <h3 className="text-sm font-medium text-zinc-200">{t("files.sharedUpdateTitle")}</h3>
                    <div className="grid gap-3 sm:grid-cols-2">
                      <div>
                        <label className="text-xs text-zinc-500">{t("files.sharedExtendDays")}</label>
                        <input
                          type="number"
                          value={extendDays}
                          onChange={(e) => setExtendDays(parseInt(e.target.value) || 7)}
                          min={1}
                          max={90}
                          className="w-full mt-1 rounded border border-zinc-800 bg-zinc-950 px-2 py-1.5 text-sm text-zinc-100"
                        />
                      </div>
                      <div>
                        <label className="text-xs text-zinc-500">{t("files.sharedNewExpectedSize")}</label>
                        <input
                          type="number"
                          value={newExpectedSize}
                          onChange={(e) => setNewExpectedSize(parseInt(e.target.value) || 10)}
                          min={1}
                          max={800}
                          className="w-full mt-1 rounded border border-zinc-800 bg-zinc-950 px-2 py-1.5 text-sm text-zinc-100"
                        />
                      </div>
                    </div>
                    <button
                      type="button"
                      onClick={() => handleUpdateSharedFolder()}
                      disabled={loadingShared}
                      className="bg-blue-600 hover:bg-blue-500 text-white px-4 py-1.5 rounded text-sm font-medium"
                    >
                      {t("files.sharedUpdateButton")}
                    </button>
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </section>

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

      {createdSharedToken && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4">
          <div className="w-full max-w-xl rounded-xl border border-zinc-800 bg-zinc-900 p-6">
            <h3 className="text-lg font-semibold text-white">{t("files.sharedTokenTitle")}</h3>
            <p className="text-sm text-zinc-400 mt-2">{t("files.sharedTokenHint")}</p>
            <div className="mt-4 rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2">
              <CopyableText text={createdSharedToken} variant="text" className="font-mono text-xs text-zinc-200 break-all" />
            </div>
            <div className="mt-5 flex justify-end gap-3">
              <button
                type="button"
                onClick={() => void handleCopy(createdSharedToken)}
                className="inline-flex items-center gap-2 rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-200 hover:border-zinc-700 hover:text-white"
              >
                <Copy className="h-4 w-4" />
                {t("action.copy")}
              </button>
              <button
                type="button"
                onClick={() => setCreatedSharedToken(null)}
                className="inline-flex items-center gap-2 rounded-lg bg-blue-600 px-3 py-2 text-sm font-medium text-white hover:bg-blue-500"
              >
                {t("common.ok")}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
