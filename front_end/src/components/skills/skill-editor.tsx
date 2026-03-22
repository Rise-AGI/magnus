// front_end/src/components/skills/skill-editor.tsx
"use client";

import { useState, useEffect, useRef } from "react";
import { Loader2, Dna, RefreshCw, Save, Check, Plus, Trash2, FileText, ImageIcon } from "lucide-react";
import { Drawer } from "@/components/ui/drawer";
import { ConfigClipboard } from "@/components/ui/config-clipboard";
import { HelpButton } from "@/components/ui/help-button";
import { SkillEditorHelp } from "@/components/ui/help-content";
import { CodeEditor } from "@/components/ui/code-editor";
import { ConfirmationDialog } from "@/components/ui/confirmation-dialog";
import { useEditorState } from "@/hooks/use-editor-state";
import { useLanguage } from "@/context/language-context";
import { client } from "@/lib/api";

interface SkillFileInput {
  path: string;
  content: string;
}

interface EditorData {
  id: string;
  title: string;
  description: string;
  files: SkillFileInput[];
}

interface SkillEditorProps {
  isOpen: boolean;
  mode: "create" | "clone";
  initialData: EditorData;
  initialResources?: { path: string }[];
  onClose: () => void;
  onSave: (data: EditorData) => Promise<void>;
}

const DEFAULT_SKILL_MD = `# Skill Name

Describe what this skill does and when to use it.
`;

export const DEFAULT_EDITOR_DATA: EditorData = {
  id: "",
  title: "",
  description: "",
  files: [{ path: "SKILL.md", content: DEFAULT_SKILL_MD }],
};

export function SkillEditor({ isOpen, mode, initialData, initialResources, onClose, onSave }: SkillEditorProps) {
  const { t } = useLanguage();
  const [activeFileIdx, setActiveFileIdx] = useState(0);

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
      if (!data.title.trim()) return { field: "title", message: t("skillEditor.nameRequired"), scrollTo: "field-title" };
      if (!data.id.trim()) return { field: "id", message: t("skillEditor.idRequired"), scrollTo: "field-id" };

      const hasSkillMd = data.files.some(f => f.path === "SKILL.md");
      if (!hasSkillMd) return { field: "files", message: t("skillEditor.skillMdRequired") };

      // File path validation
      for (let i = 0; i < data.files.length; i++) {
        if (!data.files[i].path.trim()) {
          return { field: "files", message: t("skillEditor.filePathEmpty") };
        }
      }
      const paths = data.files.map(f => f.path.trim());
      const seen = new Set<string>();
      for (const p of paths) {
        if (seen.has(p)) return { field: "files", message: t("skillEditor.filePathDuplicate", { v: p }) };
        seen.add(p);
      }

      return null;
    },
    labels: {
      discardTitle: t("editor.unsavedTitle"),
      discardConfirm: t("editor.unsavedChanges"),
      discardBtn: t("editor.discardBtn"),
      saveFailed: t("editor.saveFailed"),
    },
  });

  // Reset active states on open
  useEffect(() => {
    if (isOpen) {
      setActiveFileIdx(0);
      setActiveResourcePath(null);
      setResources(initialResources || []);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen]);

  const isOriginalId = mode === "clone" && formData.id === initialData.id;

  const handleGetPayload = () => ({
    id: formData.id,
    title: formData.title,
    description: formData.description,
    files: formData.files,
  });

  const handleApplyPayload = (payload: any) => {
    if (!payload || typeof payload !== "object") return;
    setFormData(prev => {
      const next = { ...prev };
      if (payload.id != null) next.id = payload.id;
      if (payload.title != null) next.title = payload.title;
      if (payload.description != null) next.description = payload.description;
      if (Array.isArray(payload.files)) next.files = payload.files;
      return next;
    });
    if (Array.isArray(payload.files)) {
      setActiveFileIdx(0);
    }
    setTimeout(() => {
      actionRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
    }, 100);
  };

  const updateFile = (idx: number, key: "path" | "content", value: string) => {
    setFormData(prev => {
      const files = [...prev.files];
      files[idx] = { ...files[idx], [key]: value };
      return { ...prev, files };
    });
  };

  const addFile = () => {
    const newIdx = formData.files.length;
    setFormData(prev => ({
      ...prev,
      files: [...prev.files, { path: "", content: "" }],
    }));
    setActiveFileIdx(newIdx);
    setActiveResourcePath(null);
  };

  const removeFile = (idx: number) => {
    if (formData.files[idx].path === "SKILL.md") return;
    setFormData(prev => ({
      ...prev,
      files: prev.files.filter((_, i) => i !== idx),
    }));
    setActiveFileIdx(0);
    setActiveResourcePath(null);
  };

  const actionRef = useRef<HTMLDivElement>(null);
  const descriptionRef = useRef<HTMLTextAreaElement>(null);
  const imageInputRef = useRef<HTMLInputElement>(null);
  const [isUploadingImage, setIsUploadingImage] = useState(false);
  const [resources, setResources] = useState<{ path: string }[]>([]);
  const [activeResourcePath, setActiveResourcePath] = useState<string | null>(null);
  const [isDeletingResource, setIsDeletingResource] = useState(false);

  const ALLOWED_IMAGE_EXTENSIONS = ".png,.jpg,.jpeg,.webp,.gif";

  const handleImageUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    e.target.value = "";

    const skillId = formData.id.trim();
    if (!skillId) return;

    setIsUploadingImage(true);
    try {
      const formPayload = new FormData();
      formPayload.append("file", file);
      await client(`/api/skills/${skillId}/resources`, { method: "POST", body: formPayload });
      setResources(prev => prev.some(r => r.path === file.name) ? prev : [...prev, { path: file.name }]);
      setActiveResourcePath(file.name);
    } catch (err: any) {
      console.error("Image upload failed:", err);
    } finally {
      setIsUploadingImage(false);
    }
  };

  const handleDeleteResource = async (path: string) => {
    const skillId = formData.id.trim();
    if (!skillId) return;

    setIsDeletingResource(true);
    try {
      await client(`/api/skills/${skillId}/resources/${path}`, { method: "DELETE" });
      setResources(prev => prev.filter(r => r.path !== path));
      if (activeResourcePath === path) setActiveResourcePath(null);
    } catch (err: any) {
      console.error("Resource delete failed:", err);
    } finally {
      setIsDeletingResource(false);
    }
  };

  useEffect(() => {
    if (descriptionRef.current) {
      descriptionRef.current.style.height = "auto";
      descriptionRef.current.style.height = `${descriptionRef.current.scrollHeight}px`;
    }
  }, [formData.description]);

  return (
    <Drawer
      isOpen={isOpen}
      onClose={guardedClose}
      title={mode === "create" ? t("skillEditor.create") : t("skillEditor.cloneUpdate")}
      icon={mode === "create" ? <Dna className="w-5 h-5 text-blue-500" /> : <RefreshCw className="w-5 h-5 text-purple-500" />}
      width="w-full max-w-4xl"
      actions={
        <>
          <HelpButton title={t("skillEditor.help")}>
            <SkillEditorHelp />
          </HelpButton>
          <ConfigClipboard
            kind="magnus/skill"
            onGetPayload={handleGetPayload}
            onApplyPayload={handleApplyPayload}
          />
        </>
      }
    >
      <div className="flex flex-col min-h-full relative">
        {showSaveToast && (
          <div className={`fixed top-[22px] left-1/2 -translate-x-1/2 z-[110] bg-emerald-500/15 border border-emerald-500/30 text-emerald-400 px-5 py-2 rounded-lg text-sm font-medium flex items-center gap-2 shadow-2xl backdrop-blur-sm transition-opacity duration-500 ${toastFading ? "opacity-0" : "opacity-100"}`}>
            <Check className="w-4 h-4" />
            {t("skillEditor.saved")}
          </div>
        )}
        <div className="flex-1 space-y-8 pb-4">
          {/* Basic Info */}
          <div className="max-w-3xl mx-auto space-y-6">
            <h3 className="text-zinc-200 text-sm font-semibold flex items-center gap-2">
              {t("skillEditor.basicInfo")}
              <div className="h-px bg-zinc-800 flex-grow ml-2"></div>
            </h3>

            <div id="field-title">
              <label className={`text-xs uppercase tracking-wider mb-1.5 block font-medium ${errorField === "title" ? "text-red-500" : "text-zinc-500"}`}>
                {t("skillEditor.name")} <span className="text-red-500">*</span>
              </label>
              <input
                value={formData.title}
                onChange={e => { setFormData(prev => ({ ...prev, title: e.target.value })); clearError("title"); }}
                placeholder="My Skill"
                className={`w-full bg-zinc-950 border px-4 py-2.5 rounded-lg text-zinc-200 text-sm focus:border-blue-500 outline-none transition-all placeholder-zinc-700
                    ${errorField === "title" ? "animate-shake border-red-500" : "border-zinc-800"}`}
              />
            </div>

            <div id="field-id">
              <label className={`text-xs uppercase tracking-wider mb-1.5 block font-medium ${errorField === "id" ? "text-red-500" : "text-zinc-500"}`}>
                {t("skillEditor.id")} <span className="text-red-500">*</span>
              </label>
              <input
                value={formData.id}
                onChange={e => { setFormData(prev => ({ ...prev, id: e.target.value })); clearError("id"); }}
                placeholder="e.g. data-analysis"
                className={`w-full bg-zinc-950 border px-4 py-2.5 rounded-lg text-zinc-200 text-sm focus:border-blue-500 outline-none transition-all placeholder-zinc-700
                    ${errorField === "id" ? "animate-shake border-red-500" : "border-zinc-800"}`}
              />
              <p className="text-[10px] text-zinc-600 mt-1">{t("skillEditor.idHint")}</p>
            </div>

            <div id="field-description">
              <label className={`text-xs uppercase tracking-wider mb-1.5 block font-medium ${errorField === "description" ? "text-red-500" : "text-zinc-500"}`}>
                {t("skills.table.description")} <span className="text-zinc-600 normal-case ml-1">({t("common.optional")})</span>
              </label>
              <textarea
                ref={descriptionRef}
                value={formData.description}
                onChange={e => { setFormData(prev => ({ ...prev, description: e.target.value })); clearError("description"); }}
                placeholder="Brief description..."
                maxLength={500}
                rows={1}
                className={`w-full bg-zinc-950 border px-4 py-2.5 rounded-lg text-zinc-200 text-sm focus:border-blue-500 outline-none transition-all placeholder-zinc-700 resize-none overflow-hidden min-h-[42px]
                    ${errorField === "description" ? "animate-shake border-red-500" : "border-zinc-800"}`}
              />
              <p className="text-[10px] text-zinc-600 mt-1">{formData.description.length}/500</p>
            </div>
          </div>

          {/* Files Section */}
          <div className="max-w-3xl mx-auto w-full">
            <h3 className="text-zinc-200 text-sm font-semibold mb-4 flex items-center gap-2">
              {t("skillEditor.filesSection")}
              <div className="h-px bg-zinc-800 flex-grow ml-2"></div>
            </h3>

            {/* File Tabs */}
            <div className="flex items-center gap-1 mb-4 flex-wrap">
              {formData.files.map((file, idx) => (
                <button
                  key={idx}
                  onClick={() => { setActiveFileIdx(idx); setActiveResourcePath(null); }}
                  className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
                    idx === activeFileIdx && !activeResourcePath
                      ? "bg-blue-600/10 text-blue-400 border border-blue-600/20"
                      : "bg-zinc-900 text-zinc-400 border border-zinc-800 hover:bg-zinc-800"
                  }`}
                >
                  <FileText className="w-3 h-3" />
                  {file.path || <span className="text-red-400 italic">{t("skillEditor.filePathUntitled")}</span>}
                </button>
              ))}
              {resources.map(r => (
                <button
                  key={`res-${r.path}`}
                  onClick={() => setActiveResourcePath(r.path)}
                  className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
                    activeResourcePath === r.path
                      ? "bg-blue-600/10 text-blue-400 border border-blue-600/20"
                      : "bg-zinc-900 text-zinc-400 border border-zinc-800 hover:bg-zinc-800"
                  }`}
                >
                  <ImageIcon className="w-3 h-3" />
                  {r.path}
                </button>
              ))}
              <button
                onClick={addFile}
                className="flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-medium bg-zinc-900 text-zinc-500 border border-zinc-800 hover:bg-zinc-800 hover:text-zinc-300 transition-colors"
              >
                <Plus className="w-3 h-3" />
                {t("skillEditor.addFile")}
              </button>
              {isOriginalId && (
                <button
                  onClick={() => imageInputRef.current?.click()}
                  disabled={isUploadingImage}
                  className="flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-medium bg-zinc-900 text-zinc-500 border border-zinc-800 hover:bg-zinc-800 hover:text-zinc-300 transition-colors disabled:opacity-50"
                  title={t("skillEditor.addImage")}
                >
                  {isUploadingImage ? <Loader2 className="w-3 h-3 animate-spin" /> : <ImageIcon className="w-3 h-3" />}
                  {t("skillEditor.addImage")}
                </button>
              )}
              <input
                ref={imageInputRef}
                type="file"
                accept={ALLOWED_IMAGE_EXTENSIONS}
                onChange={handleImageUpload}
                className="hidden"
              />
            </div>

            {/* Active File Editor or Resource Preview */}
            {activeResourcePath ? (
              <div className="space-y-3">
                <div className="flex items-center gap-3">
                  <div className="flex-1">
                    <label className="text-xs uppercase tracking-wider mb-1 block font-medium text-zinc-500">{t("skillEditor.fileName")}</label>
                    <div className="w-full bg-zinc-950 border border-zinc-800 px-4 py-2 rounded-lg text-zinc-400 text-sm font-mono">
                      {activeResourcePath}
                    </div>
                  </div>
                  <button
                    onClick={() => handleDeleteResource(activeResourcePath)}
                    disabled={isDeletingResource}
                    className="mt-5 p-2 bg-red-950/30 hover:bg-red-900/50 text-red-400 hover:text-red-300 rounded-lg transition-colors border border-red-900/30 disabled:opacity-50"
                    title={t("common.delete")}
                  >
                    {isDeletingResource ? <Loader2 className="w-4 h-4 animate-spin" /> : <Trash2 className="w-4 h-4" />}
                  </button>
                </div>
                <div className="rounded-xl overflow-hidden border border-zinc-800 bg-[#0c0c0e] min-h-[300px] flex items-center justify-center p-4">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={`/api/skills/${formData.id}/files/${activeResourcePath}?token=${typeof window !== "undefined" ? localStorage.getItem("magnus_token") || "" : ""}`}
                    alt={activeResourcePath}
                    className="rounded-md max-w-full max-h-[500px] object-contain"
                  />
                </div>
              </div>
            ) : formData.files[activeFileIdx] && (
              <div className="space-y-3">
                <div className="flex items-center gap-3">
                  <div className="flex-1">
                    <label className={`text-xs uppercase tracking-wider mb-1 block font-medium ${errorField === "files" ? "text-red-500" : "text-zinc-500"}`}>{t("skillEditor.fileName")}</label>
                    <input
                      value={formData.files[activeFileIdx].path}
                      onChange={e => { updateFile(activeFileIdx, "path", e.target.value); clearError("files"); }}
                      disabled={formData.files[activeFileIdx].path === "SKILL.md"}
                      placeholder="e.g. tools/search.py"
                      className="w-full bg-zinc-950 border border-zinc-800 px-4 py-2 rounded-lg text-zinc-200 text-sm focus:border-blue-500 outline-none transition-all placeholder-zinc-700 disabled:opacity-50 disabled:cursor-not-allowed"
                    />
                  </div>
                  {formData.files[activeFileIdx].path !== "SKILL.md" && (
                    <button
                      onClick={() => removeFile(activeFileIdx)}
                      className="mt-5 p-2 bg-red-950/30 hover:bg-red-900/50 text-red-400 hover:text-red-300 rounded-lg transition-colors border border-red-900/30"
                      title={t("common.delete")}
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  )}
                </div>
                <div>
                  <label className="text-xs uppercase tracking-wider mb-1 block font-medium text-zinc-500">{t("skillEditor.fileContent")}</label>
                  <div className="rounded-xl overflow-hidden border border-zinc-800 bg-[#1e1e1e] focus-within:ring-1 focus-within:ring-blue-500/50 transition-all min-h-[300px]">
                    <CodeEditor
                      value={formData.files[activeFileIdx].content}
                      onChange={v => updateFile(activeFileIdx, "content", v)}
                      filename={formData.files[activeFileIdx].path}
                    />
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>

        <div ref={actionRef} className="mt-auto pt-6 border-t border-zinc-800 flex flex-col-reverse sm:flex-row sm:justify-between sm:items-center gap-4 pb-1">
          {errorMessage ? (
            <span className="text-red-500 text-xs font-bold animate-pulse">{errorMessage}</span>
          ) : (
            <span className="text-zinc-500 text-xs hidden sm:block">
               {isOriginalId ? t("skillEditor.updating") : t("skillEditor.creating")}
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
                 isOriginalId ? <Save className="w-4 h-4" /> : (mode === "create" ? <Dna className="w-4 h-4" /> : <RefreshCw className="w-4 h-4" />)
              )}
              {isOriginalId ? t("skillEditor.updateBtn") : (mode === "create" ? t("skillEditor.createBtn") : t("skillEditor.cloneBtn"))}
            </button>
          </div>
        </div>
      </div>
      <ConfirmationDialog {...discardDialogProps} />
    </Drawer>
  );
}
