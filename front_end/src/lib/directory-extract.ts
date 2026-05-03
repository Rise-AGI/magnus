"use client";

const TAR_BLOCK_SIZE = 512;
const decoder = new TextDecoder("utf-8");

export interface ExtractedTarEntry {
  path: string;
  content: Uint8Array;
}

function readNullTerminated(buffer: Uint8Array, offset: number, length: number) {
  const slice = buffer.subarray(offset, offset + length);
  let end = 0;
  while (end < slice.length && slice[end] !== 0) end += 1;
  return decoder.decode(slice.subarray(0, end));
}

function readOctal(buffer: Uint8Array, offset: number, length: number): number {
  const raw = readNullTerminated(buffer, offset, length).trim();
  if (!raw) return 0;
  return parseInt(raw, 8);
}

function isAllZero(buffer: Uint8Array): boolean {
  for (let i = 0; i < buffer.length; i += 1) {
    if (buffer[i] !== 0) return false;
  }
  return true;
}

async function readAll(stream: ReadableStream<Uint8Array>): Promise<Uint8Array> {
  const reader = stream.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    if (!value) continue;
    chunks.push(value);
    total += value.length;
  }
  const out = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    out.set(chunk, offset);
    offset += chunk.length;
  }
  return out;
}

async function gunzip(blob: Blob): Promise<Uint8Array> {
  const Ctor = (globalThis as { DecompressionStream?: new (format: string) => { readable: ReadableStream<Uint8Array>; writable: WritableStream<BufferSource> } }).DecompressionStream;
  if (!Ctor) {
    throw new Error("Gzip decompression is not supported in this browser");
  }
  const decompressed = blob.stream().pipeThrough(new Ctor("gzip"));
  return readAll(decompressed);
}

function parseTar(buffer: Uint8Array): ExtractedTarEntry[] {
  const entries: ExtractedTarEntry[] = [];
  let offset = 0;

  while (offset + TAR_BLOCK_SIZE <= buffer.length) {
    const header = buffer.subarray(offset, offset + TAR_BLOCK_SIZE);

    if (isAllZero(header)) {
      const next = buffer.subarray(offset + TAR_BLOCK_SIZE, offset + 2 * TAR_BLOCK_SIZE);
      if (next.length < TAR_BLOCK_SIZE || isAllZero(next)) break;
    }

    const name = readNullTerminated(header, 0, 100);
    const size = readOctal(header, 124, 12);
    const typeflag = String.fromCharCode(header[156] || 0).replace("\0", "");
    const prefix = readNullTerminated(header, 345, 155);
    const fullPath = prefix ? `${prefix}/${name}` : name;

    offset += TAR_BLOCK_SIZE;

    if (!fullPath) {
      const padding = size > 0 ? Math.ceil(size / TAR_BLOCK_SIZE) * TAR_BLOCK_SIZE : 0;
      offset += padding;
      continue;
    }

    const isFile = typeflag === "" || typeflag === "0";
    const dataEnd = offset + size;

    if (isFile && dataEnd <= buffer.length) {
      const content = buffer.slice(offset, dataEnd);
      entries.push({ path: fullPath, content });
    }

    offset = dataEnd;
    const remainder = size % TAR_BLOCK_SIZE;
    if (remainder !== 0) {
      offset += TAR_BLOCK_SIZE - remainder;
    }
  }

  return entries;
}

export async function extractTarGz(blob: Blob): Promise<ExtractedTarEntry[]> {
  const bytes = await gunzip(blob);
  return parseTar(bytes);
}

export function isDirectoryWriteSupported(): boolean {
  return typeof window !== "undefined"
    && typeof (window as unknown as { showDirectoryPicker?: unknown }).showDirectoryPicker === "function";
}
