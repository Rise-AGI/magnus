"use client";

const TAR_BLOCK_SIZE = 512;
const TAR_TRAILER_BLOCKS = 2;
const encoder = new TextEncoder();

interface DirectoryFileEntry {
  archivePath: string;
  file: File;
}

export interface DirectoryArchiveResult {
  blob: Blob;
  uploadName: string;
  rootName: string;
  fileCount: number;
  originalSize: number;
  format: "tar.gz" | "tar";
}

function byteLength(value: string): number {
  return encoder.encode(value).length;
}

function writeString(buffer: Uint8Array, offset: number, length: number, value: string) {
  const bytes = encoder.encode(value);
  if (bytes.length > length) {
    throw new Error(`Value is too long for tar header field: ${value}`);
  }
  buffer.set(bytes, offset);
}

function writeOctal(buffer: Uint8Array, offset: number, length: number, value: number) {
  const encoded = encoder.encode(Math.max(0, Math.floor(value)).toString(8));
  if (encoded.length > length - 1) {
    throw new Error(`Numeric value is too large for tar header: ${value}`);
  }

  const start = offset + length - 1 - encoded.length;
  buffer.fill(0, offset, offset + length);
  buffer.set(encoded, start);
}

function splitTarPath(path: string) {
  if (byteLength(path) <= 100) {
    return { name: path, prefix: "" };
  }

  let best: { name: string; prefix: string } | null = null;
  for (let i = 0; i < path.length; i += 1) {
    if (path[i] !== "/") continue;
    const prefix = path.slice(0, i);
    const name = path.slice(i + 1);
    if (byteLength(prefix) <= 155 && byteLength(name) <= 100) {
      best = { name, prefix };
    }
  }

  if (!best) {
    throw new Error(`Path is too long to archive safely: ${path}`);
  }

  return best;
}

function createTarHeader(path: string, size: number, type: "0" | "5", mtime: number) {
  const buffer = new Uint8Array(TAR_BLOCK_SIZE);
  const { name, prefix } = splitTarPath(path);

  writeString(buffer, 0, 100, name);
  writeOctal(buffer, 100, 8, type === "5" ? 0o755 : 0o644);
  writeOctal(buffer, 108, 8, 0);
  writeOctal(buffer, 116, 8, 0);
  writeOctal(buffer, 124, 12, size);
  writeOctal(buffer, 136, 12, mtime);
  buffer.fill(0x20, 148, 156);
  writeString(buffer, 156, 1, type);
  writeString(buffer, 257, 6, "ustar");
  writeString(buffer, 263, 2, "00");
  writeString(buffer, 265, 6, "magnus");
  writeString(buffer, 297, 6, "magnus");
  if (prefix) {
    writeString(buffer, 345, 155, prefix);
  }

  const checksum = buffer.reduce((sum, value) => sum + value, 0);
  const checksumString = checksum.toString(8).padStart(6, "0");
  writeString(buffer, 148, 6, checksumString);
  buffer[154] = 0;
  buffer[155] = 0x20;

  return buffer;
}

function normalizeArchivePath(path: string) {
  const normalized = path.replaceAll("\\", "/").split("/").filter(Boolean);
  if (!normalized.length) {
    throw new Error("Directory upload is missing relative paths");
  }
  if (normalized.some((segment) => segment === "." || segment === "..")) {
    throw new Error("Directory upload contains unsafe relative paths");
  }
  return normalized.join("/");
}

function concatChunks(chunks: Uint8Array[]) {
  const totalLength = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
  const output = new Uint8Array(totalLength);
  let offset = 0;
  for (const chunk of chunks) {
    output.set(chunk, offset);
    offset += chunk.length;
  }
  return output;
}

function buildDirectoryEntries(files: File[]) {
  const entries: DirectoryFileEntry[] = files.map((file) => {
    const relativePath = normalizeArchivePath(file.webkitRelativePath || file.name);
    return { archivePath: relativePath, file };
  });

  const rootName = entries[0]?.archivePath.split("/")[0];
  if (!rootName) {
    throw new Error("Failed to determine directory name");
  }

  return {
    rootName,
    entries,
    directories: Array.from(
      new Set(
        entries.flatMap(({ archivePath }) => {
          const parts = archivePath.split("/");
          return parts.slice(0, -1).map((_, index) => `${parts.slice(0, index + 1).join("/")}/`);
        }),
      ),
    ).sort((a, b) => a.split("/").length - b.split("/").length),
  };
}

async function buildTarArchive(entries: DirectoryFileEntry[], directories: string[]) {
  const chunks: Uint8Array[] = [];
  const mtime = Math.floor(Date.now() / 1000);

  for (const directory of directories) {
    chunks.push(createTarHeader(directory, 0, "5", mtime));
  }

  for (const entry of entries) {
    const content = new Uint8Array(await entry.file.arrayBuffer());
    chunks.push(createTarHeader(entry.archivePath, content.length, "0", Math.floor(entry.file.lastModified / 1000) || mtime));
    chunks.push(content);

    const remainder = content.length % TAR_BLOCK_SIZE;
    if (remainder !== 0) {
      chunks.push(new Uint8Array(TAR_BLOCK_SIZE - remainder));
    }
  }

  chunks.push(new Uint8Array(TAR_BLOCK_SIZE * TAR_TRAILER_BLOCKS));
  return concatChunks(chunks);
}

async function gzipBytes(data: Uint8Array) {
  const CompressionStreamCtor = (globalThis as { CompressionStream?: new (format: string) => { readable: ReadableStream; writable: WritableStream<BufferSource> } }).CompressionStream;
  if (!CompressionStreamCtor) {
    return null;
  }

  const stream = new CompressionStreamCtor("gzip");
  const resultPromise = new Response(stream.readable).blob();
  const writer = stream.writable.getWriter();
  await writer.write(data as Uint8Array<ArrayBuffer>);
  await writer.close();
  return resultPromise;
}

export async function createDirectoryArchive(input: File[] | FileList): Promise<DirectoryArchiveResult> {
  const files = Array.isArray(input) ? input : Array.from(input);
  if (!files.length) {
    throw new Error("No files found in the selected folder");
  }

  const { rootName, entries, directories } = buildDirectoryEntries(files);
  const originalSize = files.reduce((sum, file) => sum + file.size, 0);
  const tarBytes = await buildTarArchive(entries, directories);
  const gzipped = await gzipBytes(tarBytes);

  if (gzipped) {
    return {
      blob: gzipped,
      uploadName: `${rootName}.tar.gz`,
      rootName,
      fileCount: files.length,
      originalSize,
      format: "tar.gz",
    };
  }

  return {
    blob: new Blob([tarBytes], { type: "application/x-tar" }),
    uploadName: `${rootName}.tar`,
    rootName,
    fileCount: files.length,
    originalSize,
    format: "tar",
  };
}
