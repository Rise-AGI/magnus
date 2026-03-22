// front_end/src/components/blueprints/blueprint-editor.tsx
"use client";

import { useEffect, useRef } from "react";
import { Loader2, Terminal, RefreshCw, DraftingCompass, Save, Check } from "lucide-react";
import { Drawer } from "@/components/ui/drawer";
import { ConfigClipboard } from "@/components/ui/config-clipboard";
import { HelpButton } from "@/components/ui/help-button";
import { BlueprintEditorHelp } from "@/components/ui/help-content";
import { CodeEditor } from "@/components/ui/code-editor";
import { ConfirmationDialog } from "@/components/ui/confirmation-dialog";
import { useEditorState } from "@/hooks/use-editor-state";
import { useLanguage } from "@/context/language-context";
import { BlueprintImplicitImports } from "@/lib/blueprint-defaults";

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
}

export function BlueprintEditor({ isOpen, mode, initialData, onClose, onSave }: BlueprintEditorProps) {
  const { t } = useLanguage();

  const {
    formData, setFormData,
    isSaving, errorField, errorMessage,
    clearError, showSaveToast, toastFading,
    handleButtonSave, guardedClose, discardDialogProps,
  } = useEditorState<EditorData>({
    isOpen,
    initialData,
    onSave: async (data) => {
      const trimmed = { ...data, id: data.id.trim(), title: data.title.trim(), description: data.description.trim() };
      await onSave(trimmed);
    },
    onClose,
    validate: (data) => {
      if (!data.title.trim()) return { field: "title", message: t("blueprintEditor.nameRequired"), scrollTo: "field-title" };
      if (!data.id.trim()) return { field: "id", message: t("blueprintEditor.idRequired"), scrollTo: "field-id" };
      return null;
    },
    labels: {
      discardTitle: t("editor.unsavedTitle"),
      discardConfirm: t("editor.unsavedChanges"),
      discardBtn: t("editor.discardBtn"),
      saveFailed: t("editor.saveFailed"),
    },
  });

  const isOriginalId = mode === 'clone' && formData.id === initialData.id;

  const handleGetPayload = () => ({
    id: formData.id,
    title: formData.title,
    description: formData.description,
    code: formData.code,
  });

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
      onClose={guardedClose}
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
                onChange={e => { setFormData(prev => ({ ...prev, title: e.target.value })); clearError('title'); }}
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
                onChange={e => { setFormData(prev => ({ ...prev, id: e.target.value })); clearError('id'); }}
                placeholder="e.g. my-debug-tool"
                className={`w-full bg-zinc-950 border px-4 py-2.5 rounded-lg text-zinc-200 text-sm focus:border-blue-500 outline-none transition-all placeholder-zinc-700
                    ${errorField === 'id' ? 'animate-shake border-red-500' : 'border-zinc-800'}`}
              />
              <p className="text-[10px] text-zinc-600 mt-1">{t("blueprintEditor.idHint")}</p>
            </div>

            <div id="field-description">
              <label className={`text-xs uppercase tracking-wider mb-1.5 block font-medium ${errorField === 'description' ? 'text-red-500' : 'text-zinc-500'}`}>
                {t("jobForm.description")} <span className="text-zinc-600 normal-case ml-1">({t("common.optional")})</span>
              </label>
              <textarea
                ref={descriptionRef}
                value={formData.description}
                onChange={e => { setFormData(prev => ({ ...prev, description: e.target.value })); clearError('description'); }}
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
            <label className={`text-xs uppercase font-bold mb-2 flex items-center gap-2 ${errorField === 'code' || errorField === '_submit' ? 'text-red-500' : 'text-zinc-500'}`}>
              <Terminal className="w-3 h-3" /> {t("blueprintEditor.pythonLogic")}
            </label>
            <div id="field-code" className={`relative rounded-xl overflow-hidden border bg-[#1e1e1e] focus-within:ring-1 transition-all shadow-inner min-h-[400px] ${errorField === 'code' || errorField === '_submit' ? 'border-red-500 animate-shake' : 'border-zinc-800 focus-within:ring-blue-500/50'}`}>
              <pre className="text-[13px] font-mono leading-relaxed px-6 pt-5 pb-2 text-zinc-500 border-b border-zinc-800/50 mb-0">
                <BlueprintImplicitImports />
              </pre>
              <CodeEditor
                value={formData.code}
                onChange={code => setFormData(prev => ({ ...prev, code }))}
                language="python"
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
            <button onClick={guardedClose} className="flex-1 sm:flex-none px-4 py-2.5 rounded-lg text-sm font-medium text-zinc-400 hover:text-white hover:bg-zinc-800 transition-colors">{t("common.cancel")}</button>
            <button
                onClick={handleButtonSave}
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
      <ConfirmationDialog {...discardDialogProps} />
    </Drawer>
  );
}
