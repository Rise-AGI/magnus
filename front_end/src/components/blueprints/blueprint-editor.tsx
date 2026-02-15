// front_end/src/components/blueprints/blueprint-editor.tsx
"use client";

import { useState, useEffect, useRef } from "react";
import { Loader2, Terminal, RefreshCw, DraftingCompass, Save, Check } from "lucide-react";
import { Drawer } from "@/components/ui/drawer";
import { ConfigClipboard } from "@/components/ui/config-clipboard";
import { HelpButton } from "@/components/ui/help-button";
import { BlueprintEditorHelp } from "@/components/ui/help-content";
import { useLanguage } from "@/context/language-context";
import { BlueprintImplicitImports } from "@/lib/blueprint-defaults";

import Editor from "react-simple-code-editor";
import { highlight, languages } from "prismjs";
import "prismjs/components/prism-clike";
import "prismjs/components/prism-python";
import "prismjs/themes/prism-okaidia.css";

interface EditorData {
  id: string;
  title: string;
  description: string;
  code: string;
}

interface BlueprintEditorProps {
  isOpen: boolean;
  mode: 'create' | 'clone';
  initialData: EditorData;
  onClose: () => void;
  onSave: (data: EditorData) => Promise<void>;
  isSaving: boolean;
}

export function BlueprintEditor({ isOpen, mode, initialData, onClose, onSave, isSaving }: BlueprintEditorProps) {
  const { t } = useLanguage();
  const [formData, setFormData] = useState(initialData);
  const [errorField, setErrorField] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [showSaveToast, setShowSaveToast] = useState(false);
  const [toastFading, setToastFading] = useState(false);
  const keepOpenRef = useRef(false);
  const handleSubmitRef = useRef<() => void>(() => {});
  const fadeTimerRef = useRef<ReturnType<typeof setTimeout>>();
  const hideTimerRef = useRef<ReturnType<typeof setTimeout>>();

  useEffect(() => {
    if (isOpen) {
      setFormData(initialData);
      setErrorField(null);
      setErrorMessage(null);
      setShowSaveToast(false);
      setToastFading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- intentionally omit initialData: only reset form on open, not on parent refetch
  }, [isOpen]);

  // Ctrl+S / Cmd+S: save without closing
  useEffect(() => {
    if (!isOpen) return;
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 's') {
        e.preventDefault();
        keepOpenRef.current = true;
        handleSubmitRef.current();
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [isOpen]);

  // 如果是 Clone 模式进入，且 ID 未发生变更，则视为 Update 操作
  const isOriginalId = mode === 'clone' && formData.id === initialData.id;

  const handleKeyDown = (e: React.KeyboardEvent) => {
    const target = e.currentTarget as HTMLTextAreaElement;
    const { value, selectionStart, selectionEnd } = target;

    // 找到选中区域涉及的所有行
    const firstLineStart = value.lastIndexOf('\n', selectionStart - 1) + 1;
    const lastLineEnd = value.indexOf('\n', selectionEnd);
    const blockEnd = lastLineEnd === -1 ? value.length : lastLineEnd;
    const selectedBlock = value.substring(firstLineStart, blockEnd);
    const lines = selectedBlock.split('\n');
    const hasMultipleLines = lines.length > 1 || selectionStart !== selectionEnd;

    if (e.key === 'Tab') {
      e.preventDefault();

      if (e.shiftKey) {
        // Shift+Tab: 反缩进（支持多行）
        let totalRemoved = 0;
        let firstLineRemoved = 0;
        const newLines = lines.map((line, i) => {
          const match = line.match(/^( {1,4})/);
          if (match) {
            const removed = match[1].length;
            totalRemoved += removed;
            if (i === 0) firstLineRemoved = removed;
            return line.substring(removed);
          }
          return line;
        });

        const newBlock = newLines.join('\n');
        target.setSelectionRange(firstLineStart, blockEnd);
        document.execCommand('insertText', false, newBlock);

        setTimeout(() => {
          const newStart = Math.max(firstLineStart, selectionStart - firstLineRemoved);
          const newEnd = Math.max(newStart, selectionEnd - totalRemoved);
          target.selectionStart = newStart;
          target.selectionEnd = hasMultipleLines ? newEnd : newStart;
        }, 0);
      } else {
        // Tab: 缩进（支持多行）
        if (hasMultipleLines) {
          const newLines = lines.map(line => '    ' + line);
          const newBlock = newLines.join('\n');
          target.setSelectionRange(firstLineStart, blockEnd);
          document.execCommand('insertText', false, newBlock);

          setTimeout(() => {
            target.selectionStart = selectionStart + 4;
            target.selectionEnd = selectionEnd + (lines.length * 4);
          }, 0);
        } else {
          // 单行无选中：插入 4 空格
          document.execCommand('insertText', false, "    ");
        }
      }
    }

    if ((e.metaKey || e.ctrlKey) && e.key === '/') {
      e.preventDefault();

      // 检查是否所有行都已注释
      const allCommented = lines.every(line => line.trim() === '' || line.trimStart().startsWith('#'));

      let totalDelta = 0;
      let firstLineDelta = 0;
      const newLines = lines.map((line, i) => {
        if (allCommented) {
          // 反注释
          const match = line.match(/^(\s*)# ?(.*)$/);
          if (match) {
            const newLine = match[1] + match[2];
            const delta = line.length - newLine.length;
            totalDelta -= delta;
            if (i === 0) firstLineDelta = -delta;
            return newLine;
          }
          return line;
        } else {
          // 注释
          const match = line.match(/^(\s*)(.*)$/);
          const indent = match ? match[1] : "";
          const content = match ? match[2] : line;
          if (content === '') return line;  // 空行不加注释
          const newLine = indent + "# " + content;
          totalDelta += 2;
          if (i === 0) firstLineDelta = 2;
          return newLine;
        }
      });

      const newBlock = newLines.join('\n');
      target.setSelectionRange(firstLineStart, blockEnd);
      document.execCommand('insertText', false, newBlock);

      setTimeout(() => {
        const newStart = Math.max(firstLineStart, selectionStart + firstLineDelta);
        const newEnd = hasMultipleLines ? selectionEnd + totalDelta : newStart;
        target.selectionStart = newStart;
        target.selectionEnd = newEnd;
      }, 0);
    }
  };

  const scrollToError = (id: string) => {
    const el = document.getElementById(id);
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
  };

  const clearError = (field: string) => {
    if (errorField === field) {
      setErrorField(null);
      setErrorMessage(null);
    }
  };

  const handleSubmit = async () => {
    if (isSaving) return;
    const id = formData.id.trim();
    const title = formData.title.trim();
    const description = formData.description.trim();

    setErrorField(null);
    setErrorMessage(null);

    if (!title) {
      setErrorField("title");
      setErrorMessage("⚠️ Blueprint Name is required");
      scrollToError("field-title");
      keepOpenRef.current = false;
      return;
    }
    if (!id) {
      setErrorField("id");
      setErrorMessage("⚠️ Blueprint ID is required");
      scrollToError("field-id");
      keepOpenRef.current = false;
      return;
    }
    if (!description) {
      setErrorField("description");
      setErrorMessage("⚠️ Description is required");
      scrollToError("field-description");
      keepOpenRef.current = false;
      return;
    }

    try {
      await onSave({ ...formData, id, title, description });
      if (keepOpenRef.current) {
        clearTimeout(fadeTimerRef.current);
        clearTimeout(hideTimerRef.current);
        setShowSaveToast(true);
        setToastFading(false);
        fadeTimerRef.current = setTimeout(() => setToastFading(true), 1500);
        hideTimerRef.current = setTimeout(() => { setShowSaveToast(false); setToastFading(false); }, 2000);
      } else {
        onClose();
      }
    } catch (e: any) {
      setErrorField("code");
      setErrorMessage(`⚠️ ${e.message || "Failed to save blueprint"}`);
      scrollToError("field-code");
    } finally {
      keepOpenRef.current = false;
    }
  };
  handleSubmitRef.current = handleSubmit;

  const handleGetPayload = () => {
    return {
      id: formData.id,
      title: formData.title,
      description: formData.description,
      code: formData.code,
    };
  };

  const handleApplyPayload = (payload: any) => {
    if (!payload || typeof payload !== 'object') return;

    setFormData((prev) => {
      const next = { ...prev };
      Object.keys(next).forEach((key) => {
        const k = key as keyof EditorData;
        if (payload[k] !== undefined && payload[k] !== null) {
          (next as any)[k] = payload[k];
        }
      });
      
      return next;
    });

    setTimeout(() => {
      actionRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
    }, 100);
  };

  const actionRef = useRef<HTMLDivElement>(null);
  const descriptionRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (descriptionRef.current) {
      descriptionRef.current.style.height = 'auto';
      descriptionRef.current.style.height = `${descriptionRef.current.scrollHeight}px`;
    }
  }, [formData.description]);

  return (
    <Drawer
      isOpen={isOpen}
      onClose={onClose}
      title={mode === 'create' ? t("blueprintEditor.create") : t("blueprintEditor.cloneUpdate")}
      icon={mode === 'create' ? <DraftingCompass className="w-5 h-5 text-blue-500" /> : <RefreshCw className="w-5 h-5 text-purple-500" />}
      width="w-full max-w-4xl"
      actions={
        <>
          <HelpButton title={t("blueprintEditor.help")}>
            <BlueprintEditorHelp />
          </HelpButton>
          <ConfigClipboard
            kind="magnus/blueprint"
            onGetPayload={handleGetPayload}
            onApplyPayload={handleApplyPayload}
          />
        </>
      }
    >
      <div className="flex flex-col min-h-full relative">
        {showSaveToast && (
          <div className={`fixed top-[22px] left-1/2 -translate-x-1/2 z-[110] bg-emerald-500/15 border border-emerald-500/30 text-emerald-400 px-5 py-2 rounded-lg text-sm font-medium flex items-center gap-2 shadow-2xl backdrop-blur-sm transition-opacity duration-500 ${toastFading ? 'opacity-0' : 'opacity-100'}`}>
            <Check className="w-4 h-4" />
            {t("blueprintEditor.saved")}
          </div>
        )}
        <div className="flex-1 space-y-8 pb-4">
          <div className="max-w-3xl mx-auto space-y-6">
            <h3 className="text-zinc-200 text-sm font-semibold flex items-center gap-2">
              {t("blueprintEditor.basicInfo")}
              <div className="h-px bg-zinc-800 flex-grow ml-2"></div>
            </h3>

            <div id="field-title">
              <label className={`text-xs uppercase tracking-wider mb-1.5 block font-medium ${errorField === 'title' ? 'text-red-500' : 'text-zinc-500'}`}>
                {t("blueprintEditor.name")} <span className="text-red-500">*</span>
              </label>
              <input
                value={formData.title}
                onChange={e => { setFormData({ ...formData, title: e.target.value }); clearError('title'); }}
                placeholder="My Debug Tool"
                className={`w-full bg-zinc-950 border px-4 py-2.5 rounded-lg text-zinc-200 text-sm focus:border-blue-500 outline-none transition-all placeholder-zinc-700
                    ${errorField === 'title' ? 'animate-shake border-red-500' : 'border-zinc-800'}`}
              />
            </div>

            <div id="field-id">
              <label className={`text-xs uppercase tracking-wider mb-1.5 block font-medium ${errorField === 'id' ? 'text-red-500' : 'text-zinc-500'}`}>
                {t("blueprintEditor.id")} <span className="text-red-500">*</span>
              </label>
              <input
                value={formData.id}
                onChange={e => { setFormData({ ...formData, id: e.target.value }); clearError('id'); }}
                placeholder="e.g. my-debug-tool"
                className={`w-full bg-zinc-950 border px-4 py-2.5 rounded-lg text-zinc-200 text-sm focus:border-blue-500 outline-none transition-all placeholder-zinc-700
                    ${errorField === 'id' ? 'animate-shake border-red-500' : 'border-zinc-800'}`}
              />
              <p className="text-[10px] text-zinc-600 mt-1">{t("blueprintEditor.idHint")}</p>
            </div>

            <div id="field-description">
              <label className={`text-xs uppercase tracking-wider mb-1.5 block font-medium ${errorField === 'description' ? 'text-red-500' : 'text-zinc-500'}`}>
                {t("jobForm.description")} <span className="text-red-500">*</span>
              </label>
              <textarea
                ref={descriptionRef}
                value={formData.description}
                onChange={e => { setFormData({ ...formData, description: e.target.value }); clearError('description'); }}
                placeholder="Brief description..."
                maxLength={200}
                rows={1}
                className={`w-full bg-zinc-950 border px-4 py-2.5 rounded-lg text-zinc-200 text-sm focus:border-blue-500 outline-none transition-all placeholder-zinc-700 resize-none overflow-hidden min-h-[42px]
                    ${errorField === 'description' ? 'animate-shake border-red-500' : 'border-zinc-800'}`}
              />
              <p className="text-[10px] text-zinc-600 mt-1">{formData.description.length}/200</p>
            </div>
          </div>

          <div className="flex flex-col flex-1 max-w-3xl mx-auto w-full">
            <h3 className="text-zinc-200 text-sm font-semibold mb-4 flex items-center gap-2">
              {t("blueprintEditor.implementation")}
              <div className="h-px bg-zinc-800 flex-grow ml-2"></div>
            </h3>
            <label className={`text-xs uppercase font-bold mb-2 flex items-center gap-2 ${errorField === 'code' ? 'text-red-500' : 'text-zinc-500'}`}>
              <Terminal className="w-3 h-3" /> {t("blueprintEditor.pythonLogic")}
            </label>
            <div id="field-code" className={`relative rounded-xl overflow-hidden border bg-[#1e1e1e] focus-within:ring-1 transition-all shadow-inner min-h-[400px] ${errorField === 'code' ? 'border-red-500 animate-shake' : 'border-zinc-800 focus-within:ring-blue-500/50'}`}>
              <pre className="text-[13px] font-mono leading-relaxed px-6 pt-5 pb-2 text-zinc-500 border-b border-zinc-800/50 mb-0">
                <BlueprintImplicitImports />
              </pre>
              <Editor
                value={formData.code}
                onValueChange={code => setFormData({ ...formData, code })}
                highlight={code => highlight(code, languages.python, 'python')}
                padding={24}
                onKeyDown={handleKeyDown}
                className="prism-editor font-mono text-sm leading-relaxed"
                style={{ fontFamily: '"Fira Code", "Fira Mono", monospace', fontSize: 14, backgroundColor: "transparent", minHeight: "100%" }}
                textareaClassName="focus:outline-none"
              />
            </div>
          </div>
        </div>

        <div ref={actionRef} className="mt-auto pt-6 border-t border-zinc-800 flex flex-col-reverse sm:flex-row sm:justify-between sm:items-center gap-4 pb-1">
          {errorMessage ? (
            <span className="text-red-500 text-xs font-bold animate-pulse">{errorMessage}</span>
          ) : (
            <span className="text-zinc-500 text-xs hidden sm:block">
               {isOriginalId ? t("blueprintEditor.updating") : t("blueprintEditor.creating")}
            </span>
          )}
          <div className="flex gap-3 w-full sm:w-auto">
            <button onClick={onClose} className="flex-1 sm:flex-none px-4 py-2.5 rounded-lg text-sm font-medium text-zinc-400 hover:text-white hover:bg-zinc-800 transition-colors">{t("common.cancel")}</button>
            <button
                onClick={() => { keepOpenRef.current = false; handleSubmit(); }}
                disabled={isSaving}
                className="flex-1 sm:flex-none px-6 py-2.5 rounded-lg text-sm font-medium bg-blue-600 hover:bg-blue-500 text-white shadow-lg shadow-blue-900/20 active:scale-95 transition-all flex items-center justify-center gap-2"
            >
              {isSaving ? (
                 <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                 isOriginalId ? <Save className="w-4 h-4" /> : (mode === 'create' ? <DraftingCompass className="w-4 h-4" /> : <RefreshCw className="w-4 h-4" />)
              )}
              {isOriginalId ? t("blueprintEditor.updateBtn") : (mode === 'create' ? t("blueprintEditor.createBtn") : t("blueprintEditor.cloneBtn"))}
            </button>
          </div>
        </div>
      </div>
    </Drawer>
  );
}