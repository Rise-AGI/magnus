// front_end/src/components/ui/confirmation-dialog.tsx
import { AlertTriangle, Info, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { useLanguage } from "@/context/language-context";

interface ConfirmationDialogProps {
  isOpen: boolean;
  onClose: () => void;
  onConfirm?: () => void;
  title: string;
  description: React.ReactNode;
  confirmText?: string;
  cancelText?: string;
  isLoading?: boolean;
  variant?: "danger" | "default" | "info";
  mode?: "confirm" | "alert";
  confirmInput?: string;   // 需要手动输入匹配此字符串才能确认
  confirmInputLabel?: string;
}

export function ConfirmationDialog({
  isOpen,
  onClose,
  onConfirm,
  title,
  description,
  confirmText,
  cancelText,
  isLoading = false,
  variant = "danger",
  mode = "confirm",
  confirmInput,
  confirmInputLabel,
}: ConfirmationDialogProps) {
  const { t } = useLanguage();
  const [inputValue, setInputValue] = useState("");

  const resolvedCancelText = cancelText ?? t("common.cancel");
  const resolvedConfirmText = confirmText ?? t("common.confirm");

  const needsInput = mode === "confirm" && confirmInput != null;
  const inputMatched = !needsInput || inputValue === confirmInput;

  // 打开/关闭时重置输入
  useEffect(() => {
    if (!isOpen) setInputValue("");
  }, [isOpen]);

  // 支持 ESC 关闭
  useEffect(() => {
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === "Escape" && isOpen && !isLoading) onClose();
    };
    window.addEventListener("keydown", handleEsc);
    return () => window.removeEventListener("keydown", handleEsc);
  }, [isOpen, onClose, isLoading]);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center p-4 min-h-screen">
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/60 backdrop-blur-sm transition-opacity"
        onClick={() => !isLoading && onClose()}
      />

      {/* Dialog Content */}
      <div className="relative bg-[#09090b] border border-zinc-800 rounded-xl shadow-2xl w-full max-w-md overflow-hidden transform transition-all scale-100 opacity-100">
        <div className="p-6">
          <div className="flex items-start gap-4">
            <div className={`p-3 rounded-full flex-shrink-0 ${
              variant === 'danger' ? 'bg-red-500/10 text-red-500' :
              variant === 'info' ? 'bg-amber-500/10 text-amber-500' :
              'bg-blue-500/10 text-blue-500'
            }`}>
              {variant === 'info' ? <Info className="w-6 h-6" /> : <AlertTriangle className="w-6 h-6" />}
            </div>
            <div className="flex-1">
              <h3 className="text-lg font-semibold text-zinc-100 leading-none mb-2">
                {title}
              </h3>
              <div className="text-sm text-zinc-400 leading-relaxed">
                {description}
              </div>
            </div>
          </div>

          {needsInput && (
            <div className="mt-4">
              <label className="block text-xs text-zinc-500 mb-1.5">
                {confirmInputLabel || `${t("common.typeToConfirm", { v: confirmInput! })}`}
              </label>
              <input
                type="text"
                value={inputValue}
                onChange={(e) => setInputValue(e.target.value)}
                placeholder={confirmInput}
                autoFocus
                className="w-full px-3 py-2 rounded-lg bg-zinc-900 border border-zinc-700 text-sm text-zinc-200 placeholder-zinc-600 focus:outline-none focus:border-red-500/50 focus:ring-1 focus:ring-red-500/20 font-mono"
              />
            </div>
          )}
        </div>

        <div className="bg-zinc-900/50 px-6 py-4 flex items-center justify-end gap-3 border-t border-zinc-800/50">
          {mode === "confirm" && (
            <button
              onClick={onClose}
              disabled={isLoading}
              className="px-4 py-2 rounded-lg text-sm font-medium text-zinc-300 hover:text-white hover:bg-zinc-800 transition-colors disabled:opacity-50"
            >
              {resolvedCancelText}
            </button>
          )}
          <button
            onClick={mode === "alert" ? onClose : onConfirm}
            disabled={isLoading || !inputMatched}
            className={`px-4 py-2 rounded-lg text-sm font-medium text-white shadow-lg transition-all flex items-center gap-2 disabled:opacity-70 disabled:cursor-not-allowed
              ${variant === 'danger'
                ? 'bg-red-600 hover:bg-red-500 border border-red-500/50 shadow-red-900/20'
                : 'bg-blue-600 hover:bg-blue-500 border border-blue-500/50'
              }`}
          >
            {isLoading && <Loader2 className="w-4 h-4 animate-spin" />}
            {mode === "alert" ? (resolvedConfirmText || t("common.ok")) : resolvedConfirmText}
          </button>
        </div>
      </div>
    </div>
  );
}