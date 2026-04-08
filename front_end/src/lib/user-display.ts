// front_end/src/lib/user-display.ts

function isHanCharacter(char: string): boolean {
  const codePoint = char.codePointAt(0);
  if (codePoint === undefined) return false;

  return (
    (codePoint >= 0x3400 && codePoint <= 0x4dbf) ||
    (codePoint >= 0x4e00 && codePoint <= 0x9fff) ||
    (codePoint >= 0xf900 && codePoint <= 0xfaff) ||
    (codePoint >= 0x20000 && codePoint <= 0x2a6df) ||
    (codePoint >= 0x2a700 && codePoint <= 0x2b73f) ||
    (codePoint >= 0x2b740 && codePoint <= 0x2b81f) ||
    (codePoint >= 0x2b820 && codePoint <= 0x2ceaf) ||
    (codePoint >= 0x2ceb0 && codePoint <= 0x2ebef) ||
    (codePoint >= 0x30000 && codePoint <= 0x3134f)
  );
}

export function getUserInitials(name: string): string {
  const normalized = name.trim().replace(/\s+/g, "");
  if (!normalized) return "?";

  const chars = Array.from(normalized);
  const hanCharCount = chars.filter(isHanCharacter).length;

  if (hanCharCount > chars.length / 2) {
    return chars.slice(-2).join("");
  }

  return chars.slice(0, 2).join("").toUpperCase();
}
