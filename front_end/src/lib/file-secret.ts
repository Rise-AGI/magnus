"use client";

import { API_BASE } from "@/lib/config";
import { createDirectoryArchive } from "@/lib/directory-archive";

const FILE_SECRET_PREFIX = "magnus-secret:";

export interface UploadFileSecretOptions {
  expireMinutes?: number;
  maxDownloads?: number;
}

export interface UploadFileSecretResult {
  fileSecret: string;
  fileName: string;
  size: number;
  sourceName: string;
  kind: "file" | "directory";
}

function getAuthHeaders(): HeadersInit | undefined {
  const token = typeof window !== "undefined" ? localStorage.getItem("magnus_token") : null;
  return token ? { Authorization: `Bearer ${token}` } : undefined;
}

export function normalizeFileSecret(secret: string): string {
  const trimmed = secret.trim();
  if (!trimmed) return "";
  return trimmed.startsWith(FILE_SECRET_PREFIX)
    ? trimmed
    : `${FILE_SECRET_PREFIX}${trimmed}`;
}

export function getFileSecretToken(secret: string): string {
  const normalized = normalizeFileSecret(secret);
  return normalized.startsWith(FILE_SECRET_PREFIX)
    ? normalized.slice(FILE_SECRET_PREFIX.length)
    : normalized;
}

async function uploadBlobToSecret(
  blob: Blob,
  uploadName: string,
  options: UploadFileSecretOptions = {},
  metadata: Pick<UploadFileSecretResult, "sourceName" | "kind">,
): Promise<UploadFileSecretResult> {
  const formData = new FormData();
  formData.append("file", blob, uploadName);
  if (typeof options.expireMinutes === "number") {
    formData.append("expire_minutes", String(options.expireMinutes));
  }
  if (typeof options.maxDownloads === "number") {
    formData.append("max_downloads", String(options.maxDownloads));
  }
  if (metadata.kind === "directory") {
    formData.append("is_directory", "true");
  }

  const response = await fetch(`${API_BASE}/api/files/upload`, {
    method: "POST",
    headers: getAuthHeaders(),
    body: formData,
  });

  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.detail || "Upload failed");
  }

  if (!payload.file_secret || typeof payload.file_secret !== "string") {
    throw new Error("Invalid server response");
  }

  return {
    fileSecret: payload.file_secret,
    fileName: uploadName,
    size: blob.size,
    sourceName: metadata.sourceName,
    kind: metadata.kind,
  };
}

export async function uploadFileToSecret(
  file: File,
  options: UploadFileSecretOptions = {},
): Promise<UploadFileSecretResult> {
  return uploadBlobToSecret(file, file.name, options, {
    sourceName: file.name,
    kind: "file",
  });
}

export async function uploadDirectoryToSecret(
  files: File[] | FileList,
  options: UploadFileSecretOptions = {},
): Promise<UploadFileSecretResult> {
  const archive = await createDirectoryArchive(files);
  return uploadBlobToSecret(archive.blob, archive.uploadName, options, {
    sourceName: archive.rootName,
    kind: "directory",
  });
}

function parseFileName(response: Response, fallback: string) {
  const disposition = response.headers.get("content-disposition");
  if (!disposition) return fallback;

  const utf8Match = disposition.match(/filename\*=UTF-8''([^;]+)/i);
  if (utf8Match?.[1]) {
    return decodeURIComponent(utf8Match[1]);
  }

  const basicMatch = disposition.match(/filename="?([^"]+)"?/i);
  return basicMatch?.[1] || fallback;
}

export async function downloadFileSecret(secret: string) {
  return downloadFileSecretWithOptions(secret);
}

function resolveDownloadName(actualFileName: string, suggestedName?: string) {
  const trimmed = suggestedName?.trim();
  if (!trimmed) return actualFileName;

  const clean = trimmed.replace(/[\\/]+$/, "").split(/[\\/]/).filter(Boolean).pop();
  if (!clean) return actualFileName;

  if (clean.includes(".")) {
    return clean;
  }

  if (actualFileName.endsWith(".tar.gz")) {
    return `${clean}.tar.gz`;
  }
  if (actualFileName.endsWith(".tar")) {
    return `${clean}.tar`;
  }

  const extensionIndex = actualFileName.lastIndexOf(".");
  if (extensionIndex > 0) {
    return `${clean}${actualFileName.slice(extensionIndex)}`;
  }

  return clean;
}

export async function downloadFileSecretWithOptions(
  secret: string,
  options: { suggestedName?: string } = {},
) {
  const token = getFileSecretToken(secret);
  if (!token) {
    throw new Error("Missing file secret");
  }

  const response = await fetch(`${API_BASE}/api/files/download/${encodeURIComponent(token)}`, {
    method: "GET",
    headers: getAuthHeaders(),
  });

  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || "Download failed");
  }

  const blob = await response.blob();
  const actualFileName = parseFileName(response, token);
  const fileName = resolveDownloadName(actualFileName, options.suggestedName);
  const objectUrl = window.URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = fileName;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.URL.revokeObjectURL(objectUrl);

  return { fileName, size: blob.size };
}

export function formatFileSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const exponent = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const value = bytes / 1024 ** exponent;
  return `${value >= 10 || exponent === 0 ? value.toFixed(0) : value.toFixed(1)} ${units[exponent]}`;
}
