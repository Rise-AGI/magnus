// front_end/src/app/(main)/blueprints/[id]/page.tsx
"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { useParams, useRouter } from "next/navigation";
import { 
  ArrowLeft, Terminal, Clock, DraftingCompass, RefreshCw, 
  Trash2, Play, Loader2, FileCode, FileQuestion, Check, Copy
} from "lucide-react";

import { client } from "@/lib/api";
import { formatBeijingTime, computeStableHash } from "@/lib/utils";
import { useAuth } from "@/context/auth-context";

import { CopyableText } from "@/components/ui/copyable-text";
import { ConfirmationDialog } from "@/components/ui/confirmation-dialog";
import { DynamicForm } from "@/components/ui/dynamic-form";
import { BlueprintEditor } from "@/components/blueprints/blueprint-editor";
import RenderMarkdown from "@/components/ui/render-markdown";

import { Blueprint, BlueprintPreference } from "@/types/blueprint";
import { FieldSchema } from "@/components/ui/dynamic-form/types";

// Syntax Highlighting for Read-only view
import Editor from "react-simple-code-editor";
import { highlight, languages } from "prismjs";
import "prismjs/components/prism-clike";
import "prismjs/components/prism-python";
import "prismjs/themes/prism-okaidia.css";

export default function BlueprintDetailsPage() {
  const params = useParams();
  const router = useRouter();
  const { user: currentUser } = useAuth();
  const blueprintId = params.id as string;

  // Data States
  const [blueprint, setBlueprint] = useState<Blueprint | null>(null);
  const [loading, setLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);

  // Form & Runner States
  const [paramsSchema, setParamsSchema] = useState<FieldSchema[]>([]);
  const [formValues, setFormValues] = useState<Record<string, any>>({});
  const [isLoadingSchema, setIsLoadingSchema] = useState(false);
  const [isRunning, setIsRunning] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);
  const [runFieldErr, setRunFieldErr] = useState<string | null>(null);
  
  // Action States
  const [isDeleting, setIsDeleting] = useState(false);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [editorOpen, setEditorOpen] = useState(false);
  const [editorData, setEditorData] = useState({ id: "", title: "", description: "", code: "" });
  const [isSavingClone, setIsSavingClone] = useState(false);
  
  // Copy States
  const [copiedDesc, setCopiedDesc] = useState(false);
  const [copiedCode, setCopiedCode] = useState(false);

  const currentHashRef = useRef<string>("");

  // 1. Fetch Blueprint Details
  const fetchBlueprint = useCallback(async () => {
    setLoading(true);
    try {
      const data = await client(`/api/blueprints/${blueprintId}`);
      
      const mappedData = {
          ...data,
          updatedAt: data.updated_at || data.updatedAt,
      };

      setBlueprint(mappedData);
      
      setEditorData({
        id: mappedData.id,
        title: mappedData.title,
        description: mappedData.description,
        code: mappedData.code
      });
      setNotFound(false);
    } catch (e) {
      console.error("Failed to fetch blueprint", e);
      setNotFound(true);
    } finally {
      setLoading(false);
    }
  }, [blueprintId]);

  useEffect(() => {
    fetchBlueprint();
  }, [fetchBlueprint]);

  // 2. Fetch Schema & Preferences
  useEffect(() => {
    if (!blueprint?.id) return;

    let isMounted = true;
    const loadSchemaAndPrefs = async () => {
      setIsLoadingSchema(true);
      try {
        const results = await Promise.allSettled([
          client(`/api/blueprints/${blueprint.id}/schema`),
          client(`/api/blueprints/${blueprint.id}/preference`, { cache: "no-store" })
        ]);

        if (!isMounted) return;

        const schemaResult = results[0];
        if (schemaResult.status === "rejected") {
          throw new Error(schemaResult.reason.message || "Failed to fetch schema");
        }
        
        const schema = schemaResult.value;
        let preference: BlueprintPreference | null = null;
        if (results[1].status === "fulfilled") {
          preference = results[1].value as BlueprintPreference;
        }

        const signatureHash = await computeStableHash(schema);
        currentHashRef.current = signatureHash;
        setParamsSchema(schema);

        const initial: Record<string, any> = {};
        const useCache = preference && preference.blueprint_hash === signatureHash;

        schema.forEach((p: FieldSchema) => {
          if (useCache && preference!.cached_params[p.key] !== undefined) {
            initial[p.key] = preference!.cached_params[p.key];
          } else {
            initial[p.key] = p.default ?? "";
          }
        });

        setFormValues(initial);
      } catch (e: any) {
        if (isMounted) console.error("Schema load failed", e);
      } finally {
        if (isMounted) setIsLoadingSchema(false);
      }
    };

    loadSchemaAndPrefs();
    return () => { isMounted = false; };
  }, [blueprint]);

  // Actions
  const handleRun = async () => {
    if (!blueprint) return;
    setRunError(null);
    setRunFieldErr(null);

    for (const param of paramsSchema) {
      if (param.type === 'text' && param.allow_empty === false) {
        const val = formValues[param.key];
        if (!val || (typeof val === 'string' && !val.trim())) {
          setRunFieldErr(param.key);
          setRunError(`⚠️ ${param.label || param.key} is required`);
          const el = document.getElementById(`field-${param.key}`);
          if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
          return;
        }
      }
    }

    setIsRunning(true);
    try {
      await client(`/api/blueprints/${blueprint.id}/run`, {
        method: "POST",
        json: formValues
      });

      if (currentHashRef.current) {
        client(`/api/blueprints/${blueprint.id}/preference`, {
          method: "PUT",
          json: {
            blueprint_id: blueprint.id,
            blueprint_hash: currentHashRef.current,
            cached_params: formValues,
          }
        }).catch(err => console.warn("Failed to save preference:", err));
      }

      // Flash Message for Job List
      sessionStorage.setItem('magnus_new_job', 'true');
      router.refresh();
      router.push('/jobs');
    } catch (e: any) {
      setRunError(`Execution Failed: ${e.message}`);
      setIsRunning(false);
    }
  };

  const handleEditorSave = async (data: any) => {
    setIsSavingClone(true);
    try {
      await client("/api/blueprints", { method: "POST", json: data });
      setEditorOpen(false);
      
      if (data.id === blueprint?.id) {
         // [Edit Mode] Update in place
         await fetchBlueprint();
      } else {
         // [Clone Mode] New Blueprint Created
         sessionStorage.setItem('magnus_new_blueprint', 'true');
         router.refresh();
         router.push('/blueprints');
      }
    } catch (e: any) {
      alert(e.message);
    } finally {
      setIsSavingClone(false);
    }
  };

  const handleDelete = async () => {
    if (!blueprint) return;
    setIsDeleting(true);
    try {
      await client(`/api/blueprints/${blueprint.id}`, { method: "DELETE" });
      router.push('/blueprints');
    } catch (e: any) {
      alert(e.message);
      setIsDeleting(false);
    }
  };

  const copyToClipboard = async (text: string, setCopied: (v: boolean) => void) => {
    try {
        await navigator.clipboard.writeText(text);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
    } catch (err) {
        console.error('Failed to copy', err);
    }
  };

  if (loading) return <div className="flex h-[50vh] items-center justify-center text-zinc-500"><Loader2 className="w-8 h-8 animate-spin text-blue-500" /></div>;

  if (notFound || !blueprint) {
    return (
      <div className="flex flex-col items-center justify-center h-[60vh] text-zinc-400 gap-6">
        <div className="bg-zinc-900/50 p-8 rounded-2xl border border-zinc-800 text-center max-w-md shadow-2xl backdrop-blur-sm">
          <div className="w-16 h-16 bg-zinc-800/80 rounded-full flex items-center justify-center mx-auto mb-6 border border-zinc-700/50 shadow-inner">
            <FileQuestion className="w-8 h-8 text-zinc-500" />
          </div>
          <h2 className="text-xl font-bold text-zinc-200 mb-2 tracking-tight">Blueprint Not Found</h2>
          <p className="text-zinc-500 text-sm mb-8 leading-relaxed">
            The blueprint definition <code className="bg-zinc-800 px-1.5 py-0.5 rounded text-zinc-400 font-mono text-xs">{decodeURIComponent(blueprintId)}</code> could not be located in the registry. <br/>It may have been deleted or the ID is incorrect.
          </p>
          <button
            onClick={() => router.push('/blueprints')}
            className="px-6 py-2.5 bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium rounded-lg transition-all shadow-lg shadow-blue-900/20 active:scale-95 flex items-center justify-center gap-2 mx-auto"
          >
            <ArrowLeft className="w-4 h-4" /> Return to Registry
          </button>
        </div>
      </div>
    );
  }

  const isOwner = currentUser?.id === blueprint.user_id;
  const displayUser = blueprint.user || { 
      id: blueprint.user_id, 
      name: "Unknown", 
      email: undefined, 
      avatar_url: undefined,
      feishu_open_id: ""
  };

  return (
    <div className="max-w-7xl mx-auto pb-20 px-4 lg:px-0">
      <style jsx global>{`
          .prism-editor textarea { outline: none !important; }
          code[class*="language-"], pre[class*="language-"] { text-shadow: none !important; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace !important; }
          ::-webkit-scrollbar { display: none; }
          html { -ms-overflow-style: none; scrollbar-width: none; }
      `}</style>

      {/* Navigation */}
      <div className="mb-8">
        <button onClick={() => router.push('/blueprints')} className="flex items-center gap-2 text-zinc-400 hover:text-white transition-colors text-sm mb-6 group">
          <ArrowLeft className="w-4 h-4 group-hover:-translate-x-1 transition-transform" />
          Back to Blueprints
        </button>

        {/* Header Section */}
        <div className="flex flex-col md:flex-row md:items-start justify-between gap-6">
          <div className="flex-1 min-w-0 pr-8">
            <div className="flex items-center gap-4 mb-3">
              <DraftingCompass className="w-8 h-8 text-blue-500" />
              <CopyableText text={blueprint.title} variant="text" className="!w-auto text-3xl font-bold text-white tracking-tight leading-tight" />
            </div>

            <div className="flex items-center gap-1 text-sm text-zinc-500 font-mono">
               <div className="flex items-center gap-2">
                 <span className="text-zinc-600">ID:</span>
                 <CopyableText text={blueprint.id} variant="id" />
               </div>
               <span className="text-zinc-700">|</span>
               <span className="flex items-center gap-1.5">
                 <Clock className="w-3.5 h-3.5" />
                 {formatBeijingTime(blueprint.updatedAt)}
               </span>
            </div>
          </div>

          {/* Creator Card (复刻 Job Status Card 布局) */}
          <div className="flex items-center gap-4 bg-zinc-900/50 border border-zinc-800 px-6 py-4 rounded-xl backdrop-blur-sm flex-shrink-0 shadow-lg shadow-black/20">
             <div className="flex-shrink-0">
                {displayUser.avatar_url ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img 
                    src={displayUser.avatar_url} 
                    alt={displayUser.name} 
                    className="w-10 h-10 rounded-full border border-zinc-700/50 object-cover shadow-sm"
                  />
                ) : (
                  <div className="w-10 h-10 rounded-full bg-indigo-500/20 text-indigo-400 flex items-center justify-center text-xs font-bold border border-indigo-500/30">
                    {displayUser.name.substring(0, 2).toUpperCase()}
                  </div>
                )}
             </div>
             
             <div className="flex flex-col">
                <span className="text-xs text-zinc-500 uppercase font-bold tracking-wider mb-0.5">Author</span>
                <span className="text-base font-bold tracking-wide text-zinc-200">
                   {displayUser.name}
                </span>
             </div>

             <div className="ml-4 pl-4 border-l border-zinc-700/50 h-full flex items-center gap-2">
                <button 
                    onClick={() => { setEditorOpen(true); }}
                    className="p-2 bg-zinc-800 hover:bg-zinc-700 hover:text-white rounded-lg text-zinc-400 transition-colors border border-zinc-700/50 shadow-sm"
                    title={isOwner ? "Edit / Clone Blueprint" : "Clone Blueprint"}
                >
                    <RefreshCw className="w-5 h-5" />
                </button>

                {isOwner && (
                    <button 
                        onClick={() => setShowDeleteConfirm(true)}
                        className="p-2 bg-red-950/30 hover:bg-red-900/50 text-red-400 hover:text-red-300 rounded-lg transition-colors border border-red-900/30"
                        title="Delete Blueprint"
                    >
                        <Trash2 className="w-5 h-5" />
                    </button>
                )}
             </div>
          </div>
        </div>
      </div>

      {/* Main Content - 40/60 Split */}
      <div className="grid grid-cols-1 lg:grid-cols-5 gap-6 h-[750px]">
        
        {/* Left Column (40%): Implementation & Details */}
        <div className="lg:col-span-2 flex flex-col gap-6 h-full overflow-hidden">
          
          {/* Description */}
          <div className="shrink-0 bg-zinc-900/30 border border-zinc-800 rounded-xl overflow-hidden flex flex-col max-h-[300px]">
             <div className="px-5 py-3 border-b border-zinc-800 bg-zinc-900/50 flex items-center justify-between">
               <div className="flex items-center gap-2">
                 <DraftingCompass className="w-4 h-4 text-zinc-400" />
                 <h3 className="text-sm font-semibold text-zinc-200">Description</h3>
               </div>
               <button 
                 onClick={() => copyToClipboard(blueprint.description, setCopiedDesc)}
                 className="text-zinc-500 hover:text-zinc-200 transition-colors"
                 title="Copy Description"
               >
                 {copiedDesc ? <Check className="w-3.5 h-3.5 text-green-500" /> : <Copy className="w-3.5 h-3.5" />}
               </button>
             </div>
             <div className="p-5 overflow-auto custom-scrollbar">
                <RenderMarkdown content={blueprint.description} />
             </div>
          </div>

          {/* Code Viewer (Read-only) */}
          <div className="flex-1 min-h-0 bg-zinc-900/30 border border-zinc-800 rounded-xl overflow-hidden flex flex-col">
            <div className="shrink-0 px-5 py-3 border-b border-zinc-800 bg-zinc-900/50 flex items-center justify-between">
              <div className="flex items-center gap-2">
                 <FileCode className="w-4 h-4 text-zinc-400" />
                 <h3 className="text-sm font-semibold text-zinc-200">Implementation Logic</h3>
              </div>
              <button 
                onClick={() => copyToClipboard(blueprint.code, setCopiedCode)}
                className="text-zinc-500 hover:text-zinc-200 transition-colors"
                title="Copy Code"
              >
                {copiedCode ? <Check className="w-3.5 h-3.5 text-green-500" /> : <Copy className="w-3.5 h-3.5" />}
              </button>
            </div>
            <div className="flex-1 overflow-auto bg-[#1e1e1e] relative group">
              <Editor
                value={blueprint.code}
                onValueChange={() => {}} 
                highlight={code => highlight(code, languages.python, 'python')}
                padding={20}
                className="prism-editor font-mono text-sm leading-relaxed"
                style={{ fontFamily: '"Fira Code", "Fira Mono", monospace', fontSize: 13, minHeight: "100%", pointerEvents: "none" }}
                textareaClassName="focus:outline-none"
                disabled
              />
            </div>
          </div>
        </div>

        {/* Right Column (60%): Runner Interface */}
        <div className="lg:col-span-3 h-full flex flex-col bg-[#0c0c0e] border border-zinc-800 rounded-xl overflow-hidden shadow-2xl">
           <div className="shrink-0 px-5 py-3 border-b border-zinc-800 bg-zinc-900/50 flex items-center justify-between">
             <div className="flex items-center gap-2">
                <Terminal className="w-4 h-4 text-blue-400" />
                <h3 className="text-sm font-semibold text-zinc-200">Configuration</h3>
             </div>
             {isLoadingSchema && <Loader2 className="w-3.5 h-3.5 animate-spin text-zinc-500" />}
           </div>

           <div className="flex-1 overflow-auto p-1 custom-scrollbar relative">
             <div className="p-4">
                <DynamicForm 
                  schema={paramsSchema} 
                  values={formValues} 
                  onChange={(k, v) => { setFormValues(prev => ({...prev, [k]: v})); setRunFieldErr(null); setRunError(null); }}
                  isLoading={isLoadingSchema}
                  errorField={runFieldErr}
                />
             </div>
           </div>

           <div className="shrink-0 p-5 border-t border-zinc-800 bg-zinc-900/20 flex flex-col-reverse sm:flex-row sm:justify-between sm:items-center gap-4">
              <div className="flex-1">
                 {runError ? (
                    <span className="text-red-400 text-xs font-bold animate-pulse">{runError}</span>
                 ) : (
                    <span className="text-zinc-500 text-xs">Configure parameters to instantiate this task.</span>
                 )}
              </div>
              <button 
                onClick={handleRun}
                disabled={isRunning || isLoadingSchema}
                className="px-6 py-2.5 rounded-lg text-sm font-medium bg-blue-600 hover:bg-blue-500 text-white shadow-lg shadow-blue-900/20 active:scale-95 transition-all flex items-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed min-w-[120px] justify-center"
              >
                {isRunning ? (
                   <>
                     <Loader2 className="w-4 h-4 animate-spin" /> Launching...
                   </>
                ) : (
                   <>
                     <Play className="w-4 h-4 fill-current" /> Launch
                   </>
                )}
              </button>
           </div>
        </div>

      </div>

      {/* Dialogs */}
      <BlueprintEditor 
        isOpen={editorOpen} 
        mode='clone' // 始终为 clone 模式，内部逻辑由 isOriginalId 判断是更新还是复制
        initialData={editorData} 
        onClose={() => setEditorOpen(false)} 
        onSave={handleEditorSave} 
        isSaving={isSavingClone} 
      />

      <ConfirmationDialog 
        isOpen={showDeleteConfirm} 
        onClose={() => setShowDeleteConfirm(false)} 
        onConfirm={handleDelete} 
        title="Delete Blueprint" 
        description={<span>Are you sure you want to delete blueprint <strong>{blueprint.title}</strong>? This action cannot be undone.</span>} 
        confirmText="Delete Blueprint" 
        variant="danger" 
        isLoading={isDeleting} 
      />
    </div>
  );
}