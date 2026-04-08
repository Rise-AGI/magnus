// front_end/src/app/(main)/blueprints/[id]/page.tsx
"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { useParams, useRouter } from "next/navigation";
import {
  ArrowLeft, Terminal, Clock, DraftingCompass, RefreshCw,
  Trash2, Play, Loader2, FileCode, Check, Copy
} from "lucide-react";

import { client } from "@/lib/api";
import { formatBeijingTime, computeStableHash } from "@/lib/utils";
import { useLanguage } from "@/context/language-context";
import { NotFound } from "@/components/ui/not-found";

import { CopyableText } from "@/components/ui/copyable-text";
import { ConfirmationDialog } from "@/components/ui/confirmation-dialog";
import { DynamicForm } from "@/components/ui/dynamic-form";
import { BlueprintEditor } from "@/components/blueprints/blueprint-editor";
import { CodeEditor } from "@/components/ui/code-editor";
import RenderMarkdown from "@/components/ui/render-markdown";
import { TransferableAuthor } from "@/components/ui/transferable-author";
import { useBackNavigation } from "@/hooks/use-back-navigation";

import { Blueprint, BlueprintPreference } from "@/types/blueprint";
import { FieldSchema, getFieldInitialValue, validateFieldValue } from "@/components/ui/dynamic-form/types";
import { BlueprintImplicitImports } from "@/lib/blueprint-defaults";

export default function BlueprintDetailsPage() {
  const params = useParams();
  const router = useRouter();
  const { t } = useLanguage();
  const blueprintId = params.id as string;
  const { backLabel, goBack } = useBackNavigation("/blueprints", t("blueprintDetail.backTo"));

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
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [editorOpen, setEditorOpen] = useState(false);
  const [editorData, setEditorData] = useState({ id: "", title: "", description: "", code: "" });
  
  // Copy States
  const [copiedDesc, setCopiedDesc] = useState(false);
  const [copiedCode, setCopiedCode] = useState(false);

  const currentHashRef = useRef<string>("");

  // 1. Fetch Blueprint Details
  const fetchBlueprint = useCallback(async (isBackground = false) => {
    if (!isBackground) setLoading(true);
    try {
      const data = await client(`/api/blueprints/${blueprintId}`);
      
      const mappedData = {
          ...data,
          updated_at: data.updated_at,
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
      if (!isBackground) setNotFound(true);
    } finally {
      if (!isBackground) setLoading(false);
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
        const cached = useCache ? preference!.cached_params : {};

        schema.forEach((p: FieldSchema) => {
          initial[p.key] = getFieldInitialValue(p, cached[p.key]);
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
      const err = validateFieldValue(param, formValues[param.key]);
      if (err) {
        setRunFieldErr(param.key);
        setRunError(`⚠️ ${err}`);
        const el = document.getElementById(`field-${param.key}`);
        if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        return;
      }
    }

    setIsRunning(true);
    try {
      await client(`/api/blueprints/${blueprint.id}/run`, {
        method: "POST",
        json: {
          parameters: formValues,
          use_preference: false,
          save_preference: true,
        }
      });

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
    await client("/api/blueprints", { method: "POST", json: data });

    if (data.id === blueprint?.id) {
      await fetchBlueprint(true);
    } else {
      sessionStorage.setItem('magnus_new_blueprint', 'true');
      router.refresh();
      router.push('/blueprints');
    }
  };

  const handleDelete = async () => {
    if (!blueprint) return;
    setIsDeleting(true);
    try {
      await client(`/api/blueprints/${blueprint.id}`, { method: "DELETE" });
      router.push('/blueprints');
    } catch (e: any) {
      setErrorMessage(e.message || t("common.operationFailed"));
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
      <NotFound
        title={t("blueprintDetail.notFound")}
        description={t("blueprintDetail.notFoundDesc", { id: decodeURIComponent(blueprintId) })}
        buttonText={backLabel}
        onBack={goBack}
      />
    );
  }

  const displayUser = blueprint.user || {
      id: blueprint.user_id,
      name: "Unknown",
      email: undefined,
      avatar_url: undefined,
      feishu_open_id: "",
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
        <button onClick={goBack} className="flex items-center gap-2 text-zinc-400 hover:text-white transition-colors text-sm mb-6 group">
          <ArrowLeft className="w-4 h-4 group-hover:-translate-x-1 transition-transform" />
          {backLabel}
        </button>

        {/* Header Section */}
        <div className="flex flex-col md:flex-row md:items-start justify-between gap-6">
          <div className="flex-1 min-w-0 pr-8">
            <div className="flex items-center gap-4 mb-3">
              <DraftingCompass className="w-8 h-8 text-blue-500" />
              <CopyableText text={blueprint.title} variant="text" className="text-3xl font-bold text-white tracking-tight leading-tight" />
            </div>

            <div className="flex items-center gap-1 text-sm text-zinc-500 font-mono">
               <div className="flex items-center gap-2">
                 <span className="text-zinc-600">ID:</span>
                 <CopyableText text={blueprint.id} variant="id" />
               </div>
               <span className="text-zinc-700">|</span>
               <span className="flex items-center gap-1.5">
                 <Clock className="w-3.5 h-3.5" />
                 {formatBeijingTime(blueprint.updated_at)}
               </span>
            </div>
          </div>

          {/* Creator Card */}
          <div className="flex items-center gap-4 bg-zinc-900/50 border border-zinc-800 px-6 py-4 rounded-xl backdrop-blur-sm flex-shrink-0 shadow-lg shadow-black/20">
             <TransferableAuthor
               user={displayUser}
               label={t("blueprintDetail.author")}
               canTransfer={!!blueprint.can_manage}
               entityType="blueprints"
               entityId={blueprint.id}
               entityTitle={blueprint.title}
               onTransferred={(newOwner) => setBlueprint(prev => prev ? { ...prev, user_id: newOwner.id, user: newOwner } : prev)}
             />

             <div className="ml-4 pl-4 border-l border-zinc-700/50 h-full flex items-center gap-2">
                <button
                    onClick={() => { setEditorOpen(true); }}
                    className="p-2 bg-zinc-800 hover:bg-zinc-700 hover:text-white rounded-lg text-zinc-400 transition-colors border border-zinc-700/50 shadow-sm"
                    title={blueprint.can_manage ? t("blueprintDetail.editClone") : t("blueprints.clone")}
                >
                    <RefreshCw className="w-5 h-5" />
                </button>

                <button
                    onClick={handleRun}
                    disabled={isRunning || isLoadingSchema}
                    className="p-2 bg-blue-600 hover:bg-blue-500 text-white rounded-lg transition-colors shadow-sm disabled:opacity-50 disabled:cursor-not-allowed"
                    title={t("blueprintDetail.runBlueprint")}
                >
                    {isRunning ? <Loader2 className="w-5 h-5 animate-spin" /> : <Play className="w-5 h-5 fill-current" />}
                </button>

                {blueprint.can_manage && (
                    <button
                        onClick={() => setShowDeleteConfirm(true)}
                        className="p-2 bg-red-950/30 hover:bg-red-900/50 text-red-400 hover:text-red-300 rounded-lg transition-colors border border-red-900/30"
                        title={t("blueprintDetail.deleteBlueprint")}
                    >
                        <Trash2 className="w-5 h-5" />
                    </button>
                )}
             </div>
          </div>
        </div>
      </div>

      {/* Main Content - 40/60 Split */}
      <div className="grid grid-cols-1 lg:grid-cols-5 gap-6 min-h-[500px] lg:h-[750px]">
        
        {/* Left Column (40%): Implementation & Details */}
        <div className="lg:col-span-2 flex flex-col gap-6 h-full overflow-hidden">
          
          {/* Description */}
          <div className="shrink-0 bg-zinc-900/30 border border-zinc-800 rounded-xl overflow-hidden flex flex-col max-h-[300px]">
             <div className="px-5 py-3 border-b border-zinc-800 bg-zinc-900/50 flex items-center justify-between">
               <div className="flex items-center gap-2">
                 <DraftingCompass className="w-4 h-4 text-zinc-400" />
                 <h3 className="text-sm font-semibold text-zinc-200">{t("blueprintDetail.description")}</h3>
               </div>
               <button
                 onClick={() => copyToClipboard(blueprint.description, setCopiedDesc)}
                 className="text-zinc-500 hover:text-zinc-200 transition-colors"
                 title={t("blueprintDetail.copyDescription")}
               >
                 {copiedDesc ? <Check className="w-3.5 h-3.5 text-green-500" /> : <Copy className="w-3.5 h-3.5" />}
               </button>
             </div>
             <div className="p-5 overflow-auto custom-scrollbar min-h-[60px]">
                {blueprint.description.trim() ? (
                  <RenderMarkdown content={blueprint.description} />
                ) : (
                  <p className="text-sm text-zinc-600 italic">{t("blueprintDetail.noDescription")}</p>
                )}
             </div>
          </div>

          {/* Code Viewer (Read-only) */}
          <div className="flex-1 min-h-0 bg-zinc-900/30 border border-zinc-800 rounded-xl overflow-hidden flex flex-col">
            <div className="shrink-0 px-5 py-3 border-b border-zinc-800 bg-zinc-900/50 flex items-center justify-between">
              <div className="flex items-center gap-2">
                 <FileCode className="w-4 h-4 text-zinc-400" />
                 <h3 className="text-sm font-semibold text-zinc-200">{t("blueprintDetail.implementationLogic")}</h3>
              </div>
              <button
                onClick={() => copyToClipboard(blueprint.code, setCopiedCode)}
                className="text-zinc-500 hover:text-zinc-200 transition-colors"
                title={t("blueprintDetail.copyCode")}
              >
                {copiedCode ? <Check className="w-3.5 h-3.5 text-green-500" /> : <Copy className="w-3.5 h-3.5" />}
              </button>
            </div>
            <div className="flex-1 overflow-auto bg-[#1e1e1e] relative group">
              <pre className="text-[13px] font-mono leading-relaxed px-5 pt-5 pb-2 text-zinc-500 border-b border-zinc-800/50 mb-0">
                <BlueprintImplicitImports />
              </pre>
              <CodeEditor
                value={blueprint.code}
                readOnly
                language="python"
              />
            </div>
          </div>
        </div>

        {/* Right Column (60%): Runner Interface */}
        <div className="lg:col-span-3 h-full flex flex-col bg-[#0c0c0e] border border-zinc-800 rounded-xl overflow-hidden shadow-2xl">
           <div className="shrink-0 px-5 py-3 border-b border-zinc-800 bg-zinc-900/50 flex items-center justify-between">
             <div className="flex items-center gap-2">
                <Terminal className="w-4 h-4 text-blue-400" />
                <h3 className="text-sm font-semibold text-zinc-200">{t("blueprintDetail.configuration")}</h3>
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
                    <span className="text-zinc-500 text-xs">{t("blueprintDetail.configureParams")}</span>
                 )}
              </div>
              <button
                onClick={handleRun}
                disabled={isRunning || isLoadingSchema}
                className="px-6 py-2.5 rounded-lg text-sm font-medium bg-blue-600 hover:bg-blue-500 text-white shadow-lg shadow-blue-900/20 active:scale-95 transition-all flex items-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed min-w-[120px] justify-center"
              >
                {isRunning ? (
                   <>
                     <Loader2 className="w-4 h-4 animate-spin" /> {t("blueprintRunner.launching")}
                   </>
                ) : (
                   <>
                     <Play className="w-4 h-4 fill-current" /> {t("blueprintRunner.launch")}
                   </>
                )}
              </button>
           </div>
        </div>

      </div>

      {/* Dialogs */}
      <BlueprintEditor
        isOpen={editorOpen}
        mode='clone'
        initialData={editorData}
        onClose={() => setEditorOpen(false)}
        onSave={handleEditorSave}
      />

      <ConfirmationDialog
        isOpen={showDeleteConfirm}
        onClose={() => setShowDeleteConfirm(false)}
        onConfirm={handleDelete}
        title={t("blueprintDetail.deleteBlueprint")}
        description={<span>{t("blueprintDetail.deleteConfirmDesc", { title: blueprint.title })}</span>}
        confirmText={t("blueprintDetail.deleteBlueprint")}
        variant="danger"
        isLoading={isDeleting}
        confirmInput={blueprint.id}
      />

      <ConfirmationDialog
        isOpen={!!errorMessage}
        onClose={() => setErrorMessage(null)}
        title={t("common.error")}
        description={errorMessage}
        confirmText={t("common.ok")}
        mode="alert"
        variant="danger"
      />
    </div>
  );
}