"use client";

import { normalizeFileSecret } from "@/lib/file-secret";

export interface ParsedReceiveAction {
  fileSecret: string;
  outputPath?: string;
  suggestedName?: string;
}

const FILE_SECRET_PATTERN = /\bmagnus-secret:[A-Za-z0-9-]+\b/;

function tokenizeShell(input: string): string[] | null {
  const tokens: string[] = [];
  let current = "";
  let quote: "'" | '"' | null = null;
  let escaping = false;

  for (const char of input) {
    if (escaping) {
      current += char;
      escaping = false;
      continue;
    }

    if (char === "\\") {
      escaping = true;
      continue;
    }

    if (quote) {
      if (char === quote) {
        quote = null;
      } else {
        current += char;
      }
      continue;
    }

    if (char === "'" || char === '"') {
      quote = char;
      continue;
    }

    if (/\s/.test(char)) {
      if (current) {
        tokens.push(current);
        current = "";
      }
      continue;
    }

    if (";|&`()<>".includes(char)) {
      return null;
    }

    current += char;
  }

  if (escaping || quote) {
    return null;
  }

  if (current) {
    tokens.push(current);
  }

  return tokens;
}

function pickSuggestedName(outputPath?: string) {
  if (!outputPath) return undefined;
  const trimmed = outputPath.trim().replace(/[\\/]+$/, "");
  if (!trimmed) return undefined;
  const parts = trimmed.split(/[\\/]/).filter(Boolean);
  return parts[parts.length - 1];
}

export function parseReceiveAction(action?: string | null): ParsedReceiveAction | null {
  const trimmed = action?.trim();
  if (!trimmed) return null;

  const tokens = tokenizeShell(trimmed);
  if (!tokens || tokens.length < 2) {
    return null;
  }

  if (tokens[0] !== "magnus" || tokens[1] !== "receive") {
    return null;
  }

  const fileSecret = normalizeFileSecret(tokens[2] || "");
  if (!fileSecret.startsWith("magnus-secret:")) {
    return null;
  }

  let outputPath: string | undefined;
  let index = 3;
  while (index < tokens.length) {
    const token = tokens[index];
    if (token === "--output" || token === "-o") {
      const value = tokens[index + 1];
      if (!value) return null;
      outputPath = value;
      index += 2;
      continue;
    }
    return null;
  }

  return {
    fileSecret,
    outputPath,
    suggestedName: pickSuggestedName(outputPath),
  };
}

export function extractFileSecretFromText(text?: string | null): string | null {
  const match = text?.match(FILE_SECRET_PATTERN);
  return match?.[0] ? normalizeFileSecret(match[0]) : null;
}
