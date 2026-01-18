// front_end/src/components/blueprints/blueprint-editor.tsx
"use client";

import { useState, useEffect, useRef } from "react";
import { Loader2, Terminal, RefreshCw, DraftingCompass, Save } from "lucide-react";
import { Drawer } from "@/components/ui/drawer";
import { ConfigClipboard } from "@/components/ui/config-clipboard";

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
  const [formData, setFormData] = useState(initialData);
  const [errorField, setErrorField] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  useEffect(() => {
    if (isOpen) {
      setFormData(initialData);
      setErrorField(null);
      setErrorMessage(null);
    }
  }, [isOpen, initialData]);

  // 如果是 Clone 模式进入，且 ID 未发生变更，则视为 Update 操作
  const isOriginalId = mode === 'clone' && formData.id === initialData.id;

  const handleKeyDown = (e: React.KeyboardEvent) => {
    const target = e.currentTarget as HTMLTextAreaElement;
    const { value, selectionStart, selectionEnd } = target;
    if (e.key === 'Tab') {
      e.preventDefault();
      const newValue = value.substring(0, selectionStart) + "    " + value.substring(selectionEnd);
      setFormData(prev => ({ ...prev, code: newValue }));
      setTimeout(() => { target.selectionStart = target.selectionEnd = selectionStart + 4; }, 0);
    }
    if ((e.metaKey || e.ctrlKey) && e.key === '/') {
      e.preventDefault();
      const lineStart = value.lastIndexOf('\n', selectionStart - 1) + 1;
      let lineEnd = value.indexOf('\n', selectionStart);
      if (lineEnd === -1) lineEnd = value.length;
      const currentLine = value.substring(lineStart, lineEnd);
      const isCommented = currentLine.trimStart().startsWith('#');
      let newValue;
      let newCursorPos = selectionStart;
      if (isCommented) {
        const match = currentLine.match(/^(\s*)# ?(.*)$/);
        if (match) {
          const cleanLine = match[1] + match[2];
          newValue = value.substring(0, lineStart) + cleanLine + value.substring(lineEnd);
          newCursorPos = Math.max(lineStart, selectionStart - (currentLine.length - cleanLine.length));
        } else { newValue = value; }
      } else {
        const match = currentLine.match(/^(\s*)(.*)$/);
        const indent = match ? match[1] : "";
        const content = match ? match[2] : currentLine;
        const commentedLine = indent + "# " + content;
        newValue = value.substring(0, lineStart) + commentedLine + value.substring(lineEnd);
        newCursorPos = selectionStart + 2;
      }
      setFormData(prev => ({ ...prev, code: newValue }));
      setTimeout(() => { target.selectionStart = target.selectionEnd = newCursorPos; }, 0);
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

  const handleSubmit = () => {
    const id = formData.id.trim();
    const title = formData.title.trim();
    const description = formData.description.trim();

    setErrorField(null);
    setErrorMessage(null);

    // Use setTimeout(0) to push validation to the next tick, ensuring the animate-shake class resets if re-applied
    setTimeout(() => {
      if (!title) {
        setErrorField("title");
        setErrorMessage("⚠️ Blueprint Name is required");
        scrollToError("field-title");
        return;
      }
      if (!id) {
        setErrorField("id");
        setErrorMessage("⚠️ Blueprint ID is required");
        scrollToError("field-id");
        return;
      }
      if (!description) {
        setErrorField("description");
        setErrorMessage("⚠️ Description is required");
        scrollToError("field-description");
        return;
      }

      onSave({ ...formData, id, title, description });
    }, 0);
  };

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

  return (
    <Drawer
      isOpen={isOpen}
      onClose={onClose}
      title={mode === 'create' ? "Create Blueprint" : "Clone / Update Blueprint"}
      icon={mode === 'create' ? <DraftingCompass className="w-5 h-5 text-blue-500" /> : <RefreshCw className="w-5 h-5 text-purple-500" />}
      width="w-full max-w-4xl"
      actions={
        <ConfigClipboard 
          kind="magnus/blueprint"
          onGetPayload={handleGetPayload}
          onApplyPayload={handleApplyPayload}
        />
      }
    >
      <div className="flex flex-col min-h-full">
        <div className="flex-1 space-y-8 pb-4">
          <div className="max-w-3xl mx-auto space-y-6">
            <h3 className="text-zinc-200 text-sm font-semibold flex items-center gap-2">
              Basic Information
              <div className="h-px bg-zinc-800 flex-grow ml-2"></div>
            </h3>

            <div id="field-title">
              <label className={`text-xs uppercase tracking-wider mb-1.5 block font-medium ${errorField === 'title' ? 'text-red-500' : 'text-zinc-500'}`}>
                Blueprint Name <span className="text-red-500">*</span>
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
                Blueprint ID <span className="text-red-500">*</span>
              </label>
              <input
                value={formData.id}
                onChange={e => { setFormData({ ...formData, id: e.target.value }); clearError('id'); }}
                placeholder="e.g. my-debug-tool"
                className={`w-full bg-zinc-950 border px-4 py-2.5 rounded-lg text-zinc-200 text-sm focus:border-blue-500 outline-none transition-all placeholder-zinc-700 
                    ${errorField === 'id' ? 'animate-shake border-red-500' : 'border-zinc-800'}`}
              />
              <p className="text-[10px] text-zinc-600 mt-1">Unique identifier (URL safe).</p>
            </div>

            <div id="field-description">
              <label className={`text-xs uppercase tracking-wider mb-1.5 block font-medium ${errorField === 'description' ? 'text-red-500' : 'text-zinc-500'}`}>
                Description <span className="text-red-500">*</span>
              </label>
              <input
                value={formData.description}
                onChange={e => { setFormData({ ...formData, description: e.target.value }); clearError('description'); }}
                placeholder="Brief description..."
                className={`w-full bg-zinc-950 border px-4 py-2.5 rounded-lg text-zinc-200 text-sm focus:border-blue-500 outline-none transition-all placeholder-zinc-700 
                    ${errorField === 'description' ? 'animate-shake border-red-500' : 'border-zinc-800'}`}
              />
            </div>
          </div>

          <div className="flex flex-col flex-1 max-w-3xl mx-auto w-full">
            <h3 className="text-zinc-200 text-sm font-semibold mb-4 flex items-center gap-2">
              Implementation
              <div className="h-px bg-zinc-800 flex-grow ml-2"></div>
            </h3>
            <label className="text-xs uppercase font-bold text-zinc-500 mb-2 flex items-center gap-2"><Terminal className="w-3 h-3" /> Python Logic</label>
            <div className="relative rounded-xl overflow-hidden border border-zinc-800 bg-[#1e1e1e] focus-within:ring-1 focus-within:ring-blue-500/50 transition-all shadow-inner min-h-[400px]">
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
               {isOriginalId ? "Updating existing blueprint." : "Creating new blueprint definition."}
            </span>
          )}
          <div className="flex gap-3 w-full sm:w-auto">
            <button onClick={onClose} className="flex-1 sm:flex-none px-4 py-2.5 rounded-lg text-sm font-medium text-zinc-400 hover:text-white hover:bg-zinc-800 transition-colors">Cancel</button>
            <button 
                onClick={handleSubmit} 
                disabled={isSaving} 
                className="flex-1 sm:flex-none px-6 py-2.5 rounded-lg text-sm font-medium bg-blue-600 hover:bg-blue-500 text-white shadow-lg shadow-blue-900/20 active:scale-95 transition-all flex items-center justify-center gap-2"
            >
              {isSaving ? (
                 <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                 isOriginalId ? <Save className="w-4 h-4" /> : (mode === 'create' ? <DraftingCompass className="w-4 h-4" /> : <RefreshCw className="w-4 h-4" />)
              )}
              {isOriginalId ? "Update Blueprint" : (mode === 'create' ? "Create Blueprint" : "Clone Blueprint")}
            </button>
          </div>
        </div>
      </div>
    </Drawer>
  );
}