// front_end/src/components/ui/token-input.tsx
"use client";

const MAGNUS_TOKEN_LENGTH = 35;

interface TokenInputProps {
  value: string;
  onChange: (value: string) => void;
  error: string | null;
  onClearError: () => void;
  placeholder?: string;
  label?: string;
  warning?: string;
  onSubmit?: () => void;
}

export { MAGNUS_TOKEN_LENGTH };

export function TokenInput({
  value,
  onChange,
  error,
  onClearError,
  placeholder,
  label,
  warning,
  onSubmit,
}: TokenInputProps) {
  return (
    <div className="mt-4 pt-4 border-t border-zinc-800/50">
      {label && (
        <label className="text-xs text-zinc-500 font-medium block mb-1">{label}</label>
      )}
      {warning && (
        <p className="text-xs text-red-400/80 mb-2">{warning}</p>
      )}
      <input
        type="text"
        value={value}
        onChange={(e) => { onChange(e.target.value); onClearError(); }}
        placeholder={placeholder || "sk-..."}
        maxLength={MAGNUS_TOKEN_LENGTH}
        className="w-full px-3 py-2 bg-zinc-900 border border-zinc-700 rounded-lg text-sm font-mono text-zinc-200 placeholder:text-zinc-600 focus:outline-none focus:border-blue-500/50 focus:ring-1 focus:ring-blue-500/20"
        autoFocus
        onKeyDown={(e) => { if (e.key === "Enter" && onSubmit) onSubmit(); }}
      />
      <div className="flex items-center justify-between mt-2">
        <span className={`text-xs ${value.length === MAGNUS_TOKEN_LENGTH ? "text-green-500" : "text-zinc-600"}`}>
          {value.length}/{MAGNUS_TOKEN_LENGTH}
        </span>
        {error && <span className="text-xs text-red-400">{error}</span>}
      </div>
    </div>
  );
}

export function validateToken(token: string): boolean {
  return token.startsWith("sk-") && token.length === MAGNUS_TOKEN_LENGTH;
}
