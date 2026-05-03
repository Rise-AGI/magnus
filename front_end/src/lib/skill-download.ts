"use client";

import { API_BASE } from "@/lib/config";
import { extractTarGz, isDirectoryWriteSupported, type ExtractedTarEntry } from "@/lib/directory-extract";

export type SkillDownloadMode = "folder" | "archive";

export interface SkillDownloadResult {
  mode: SkillDownloadMode;
  rootName: string;
}

interface FileSystemDirectoryHandleLike {
  name: string;
  getDirectoryHandle: (name: string, options?: { create?: boolean }) => Promise<FileSystemDirectoryHandleLike>;
  getFileHandle: (name: string, options?: { create?: boolean }) => Promise<FileSystemFileHandleLike>;
}

interface FileSystemFileHandleLike {
  createWritable: () => Promise<FileSystemWritableFileStreamLike>;
}

interface FileSystemWritableFileStreamLike {
  write: (data: BufferSource) => Promise<void>;
  close: () => Promise<void>;
}

interface ShowDirectoryPickerWindow {
  showDirectoryPicker?: (options?: { mode?: "read" | "readwrite" }) => Promise<FileSystemDirectoryHandleLike>;
}

function getAuthHeaders(): HeadersInit | undefined {
  const token = typeof window !== "undefined" ? localStorage.getItem("magnus_token") : null;
  return token ? { Authorization: `Bearer ${token}` } : undefined;
}

function parseDispositionFilename(response: Response, fallback: string): string {
  const disposition = response.headers.get("content-disposition");
  if (!disposition) return fallback;

  const utf8Match = disposition.match(/filename\*=UTF-8''([^;]+)/i);
  if (utf8Match?.[1]) {
    try {
      return decodeURIComponent(utf8Match[1]);
    } catch {
      /* fall through */
    }
  }

  const basicMatch = disposition.match(/filename="?([^";]+)"?/i);
  return basicMatch?.[1] || fallback;
}

function deriveRootName(filename: string): string {
  const stripped = filename.replace(/\.tar\.gz$/i, "").replace(/\.tgz$/i, "");
  return stripped || "skill";
}

async function fetchSkillArchive(skillId: string): Promise<{ blob: Blob; rootName: string; archiveName: string }> {
  const response = await fetch(`${API_BASE}/api/skills/${encodeURIComponent(skillId)}/archive`, {
    method: "GET",
    headers: getAuthHeaders(),
  });

  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || `Download failed (${response.status})`);
  }

  const archiveName = parseDispositionFilename(response, `${skillId}.tar.gz`);
  const blob = await response.blob();
  return { blob, archiveName, rootName: deriveRootName(archiveName) };
}

async function writeEntriesIntoDirectory(
  parent: FileSystemDirectoryHandleLike,
  entries: ExtractedTarEntry[],
): Promise<void> {
  const dirCache = new Map<string, FileSystemDirectoryHandleLike>();
  dirCache.set("", parent);

  const ensureDir = async (segments: string[]): Promise<FileSystemDirectoryHandleLike> => {
    const key = segments.join("/");
    const cached = dirCache.get(key);
    if (cached) return cached;

    const parentDir = await ensureDir(segments.slice(0, -1));
    const handle = await parentDir.getDirectoryHandle(segments[segments.length - 1], { create: true });
    dirCache.set(key, handle);
    return handle;
  };

  for (const entry of entries) {
    const parts = entry.path.split("/").filter(Boolean);
    if (parts.length === 0) continue;

    const dirSegments = parts.slice(0, -1);
    const filename = parts[parts.length - 1];
    const dir = await ensureDir(dirSegments);

    const fileHandle = await dir.getFileHandle(filename, { create: true });
    const writable = await fileHandle.createWritable();
    await writable.write(entry.content as Uint8Array<ArrayBuffer>);
    await writable.close();
  }
}

function triggerBlobDownload(blob: Blob, filename: string) {
  const objectUrl = window.URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.URL.revokeObjectURL(objectUrl);
}

export class SkillDownloadCancelled extends Error {
  constructor() {
    super("Download cancelled");
    this.name = "SkillDownloadCancelled";
  }
}

export async function downloadSkillToFolder(skillId: string): Promise<SkillDownloadResult> {
  const { blob, archiveName, rootName } = await fetchSkillArchive(skillId);

  const picker = isDirectoryWriteSupported()
    ? (window as unknown as ShowDirectoryPickerWindow).showDirectoryPicker
    : undefined;
  if (!picker) {
    triggerBlobDownload(blob, archiveName);
    return { mode: "archive", rootName };
  }

  let parentHandle: FileSystemDirectoryHandleLike;
  try {
    parentHandle = await picker({ mode: "readwrite" });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new SkillDownloadCancelled();
    }
    throw error;
  }

  const entries = await extractTarGz(blob);
  if (entries.length === 0) {
    throw new Error("Archive is empty");
  }

  await writeEntriesIntoDirectory(parentHandle, entries);
  return { mode: "folder", rootName };
}
