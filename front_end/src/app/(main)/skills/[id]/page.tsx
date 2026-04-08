// front_end/src/app/(main)/skills/[id]/page.tsx
"use client";

import { useState, useEffect, useCallback, useMemo } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import {
  ArrowLeft, Clock, Dna, RefreshCw,
  Trash2, Loader2, FileText, Check, Copy, ImageIcon
} from "lucide-react";

import { client } from "@/lib/api";
import { formatBeijingTime } from "@/lib/utils";
import { useLanguage } from "@/context/language-context";
import { NotFound } from "@/components/ui/not-found";
import { CopyableText } from "@/components/ui/copyable-text";
import { ConfirmationDialog } from "@/components/ui/confirmation-dialog";
import { SkillEditor } from "@/components/skills/skill-editor";
import { CodeEditor } from "@/components/ui/code-editor";
import RenderMarkdown from "@/components/ui/render-markdown";
import { TransferableAuthor } from "@/components/ui/transferable-author";
import { useBackNavigation } from "@/hooks/use-back-navigation";
import { Skill, SkillFile } from "@/types/skill";

export default function SkillDetailPage() {
  const params = useParams();
  const router = useRouter();
  const { t } = useLanguage();
  const skillId = params.id as string;
  const { backLabel, goBack } = useBackNavigation("/skills", t("skillDetail.backTo"));

  const [skill, setSkill] = useState<Skill | null>(null);
  const [loading, setLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);

  const [activeFile, setActiveFile] = useState<SkillFile | null>(null);

  const [isDeleting, setIsDeleting] = useState(false);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [editorOpen, setEditorOpen] = useState(false);
  const [editorData, setEditorData] = useState({ id: "", title: "", description: "", files: [{ path: "SKILL.md", content: "" }] });

  const [copiedDesc, setCopiedDesc] = useState(false);
  const [copiedFile, setCopiedFile] = useState(false);

  const fetchSkill = useCallback(async (isBackground = false) => {
    if (!isBackground) setLoading(true);
    try {
      const data = await client(`/api/skills/${skillId}`);
      const mappedData: Skill = {
          ...data,
          created_at: data.created_at,
          updated_at: data.updated_at,
      };
      setSkill(mappedData);
      setEditorData({
        id: mappedData.id,
        title: mappedData.title,
        description: mappedData.description,
        files: mappedData.files.filter(f => !f.is_binary).map(f => ({ path: f.path, content: f.content })),
      });

      const skillMd = mappedData.files.find(f => f.path === "SKILL.md");
      setActiveFile(prev => {
        if (prev) {
          const updated = mappedData.files.find(f => f.path === prev.path);
          return updated || skillMd || mappedData.files[0] || null;
        }
        return skillMd || mappedData.files[0] || null;
      });

      setNotFound(false);
    } catch (e) {
      console.error("Failed to fetch skill", e);
      if (!isBackground) setNotFound(true);
    } finally {
      if (!isBackground) setLoading(false);
    }
  }, [skillId]);

  useEffect(() => {
    fetchSkill();
  }, [fetchSkill]);

  const handleEditorSave = async (data: any) => {
    await client("/api/skills", { method: "POST", json: data });
    if (data.id === skill?.id) {
      await fetchSkill(true);
    } else {
      router.push("/skills");
    }
  };

  const handleDelete = async () => {
    if (!skill) return;
    setIsDeleting(true);
    try {
      await client(`/api/skills/${skill.id}`, { method: "DELETE" });
      router.push("/skills");
    } catch (e: any) {
      setErrorMessage(e.message || t("common.operationFailed"));
      setIsDeleting(false);
    }
  };

  const copyToClipboard = async (text: string, setter: (v: boolean) => void) => {
    try {
        await navigator.clipboard.writeText(text);
        setter(true);
        setTimeout(() => setter(false), 2000);
    } catch (err) {
        console.error("Failed to copy", err);
    }
  };

  const handleFileLink = useCallback((href: string) => {
    if (href.startsWith("magnus:///")) {
      const target = "/" + href.slice("magnus:///".length);
      const sep = target.includes("?") ? "&" : "?";
      router.push(`${target}${sep}from=/skills/${skillId}`);
      return;
    }
    if (!skill) return;
    const dir = activeFile?.path.includes("/") ? activeFile.path.substring(0, activeFile.path.lastIndexOf("/") + 1) : "";
    const parts = (dir + href).split("/");
    const resolved: string[] = [];
    for (const part of parts) {
      if (part === "..") resolved.pop();
      else if (part !== ".") resolved.push(part);
    }
    const target = skill.files.find(f => f.path === resolved.join("/"));
    if (target) setActiveFile(target);
  }, [skill, activeFile, router, skillId]);

  const sortedFiles = useMemo(() => {
    if (!skill) return [];
    return [...skill.files].sort((a, b) => {
      if (a.path === "SKILL.md") return -1;
      if (b.path === "SKILL.md") return 1;
      return a.path.localeCompare(b.path);
    });
  }, [skill]);

  const magnusLinkComponents = useMemo(() => ({
    a: ({ href, children, ...props }: any) => {
      if (href?.startsWith("magnus:///")) {
        const target = "/" + href.slice("magnus:///".length);
        const sep = target.includes("?") ? "&" : "?";
        const fullPath = `${target}${sep}from=/skills/${skillId}`;
        return (
          <Link
            href={fullPath}
            className="font-medium underline underline-offset-4 text-blue-400 hover:text-blue-300 transition-all"
            {...props}
          >
            {children}
          </Link>
        );
      }
      const isRelative = href && !href.match(/^(https?:\/\/|#|mailto:)/);
      return (
        <a
          href={isRelative ? undefined : href}
          className={`font-medium underline underline-offset-4 text-blue-400 hover:text-blue-300 transition-all${isRelative ? " cursor-pointer" : ""}`}
          onClick={isRelative ? (e: React.MouseEvent) => { e.preventDefault(); handleFileLink(href); } : undefined}
          {...props}
        >
          {children}
        </a>
      );
    },
    img: ({ src, alt, className, ...props }: any) => {
      const isRelative = src && !src.match(/^(https?:\/\/|data:)/);
      const token = typeof window !== "undefined" ? localStorage.getItem("magnus_token") : null;
      const imgSrc = isRelative
        ? `/api/skills/${skillId}/files/${src}${token ? `?token=${token}` : ""}`
        : src;
      // eslint-disable-next-line @next/next/no-img-element
      return <img src={imgSrc} alt={alt || ""} className={`rounded-md border border-zinc-800 bg-zinc-950 max-w-full h-auto ${className || ""}`} {...props} />;
    },
  }), [skillId, handleFileLink]);

  if (loading) return <div className="flex h-[50vh] items-center justify-center text-zinc-500"><Loader2 className="w-8 h-8 animate-spin text-blue-500" /></div>;

  if (notFound || !skill) {
    return (
      <NotFound
        title={t("skillDetail.notFound")}
        description={t("skillDetail.notFoundDesc", { id: decodeURIComponent(skillId) })}
        buttonText={backLabel}
        onBack={goBack}
      />
    );
  }

  const displayUser = skill.user || {
      id: skill.user_id,
      name: "Unknown",
      email: undefined,
      avatar_url: undefined,
      feishu_open_id: "",
  };

  const isMarkdown = activeFile?.path.endsWith(".md");
  const isImage = !!activeFile?.is_binary;

  return (
    <div className="max-w-7xl mx-auto pb-20 px-4 lg:px-0">
      <style jsx global>{`
          ::-webkit-scrollbar { display: none; }
          html { -ms-overflow-style: none; scrollbar-width: none; }
      `}</style>

      {/* Navigation */}
      <div className="mb-8">
        <button onClick={goBack} className="flex items-center gap-2 text-zinc-400 hover:text-white transition-colors text-sm mb-6 group">
          <ArrowLeft className="w-4 h-4 group-hover:-translate-x-1 transition-transform" />
          {backLabel}
        </button>

        {/* Header */}
        <div className="flex flex-col md:flex-row md:items-start justify-between gap-6">
          <div className="flex-1 min-w-0 pr-8">
            <div className="flex items-center gap-4 mb-3">
              <Dna className="w-8 h-8 text-blue-500" />
              <CopyableText text={skill.title} variant="text" className="text-3xl font-bold text-white tracking-tight leading-tight" />
            </div>
            <div className="flex items-center gap-1 text-sm text-zinc-500 font-mono">
               <div className="flex items-center gap-2">
                 <span className="text-zinc-600">ID:</span>
                 <CopyableText text={skill.id} variant="id" />
               </div>
               <span className="text-zinc-700">|</span>
               <span className="flex items-center gap-1.5">
                 <Clock className="w-3.5 h-3.5" />
                 {formatBeijingTime(skill.updated_at)}
               </span>
            </div>
          </div>

          {/* Creator Card */}
          <div className="flex items-center gap-4 bg-zinc-900/50 border border-zinc-800 px-6 py-4 rounded-xl backdrop-blur-sm flex-shrink-0 shadow-lg shadow-black/20">
             <TransferableAuthor
               user={displayUser}
               label={t("skillDetail.author")}
               canTransfer={!!skill.can_manage}
               entityType="skills"
               entityId={skill.id}
               entityTitle={skill.title}
               onTransferred={(newOwner) => setSkill(prev => prev ? { ...prev, user_id: newOwner.id, user: newOwner } : prev)}
             />

             <div className="ml-4 pl-4 border-l border-zinc-700/50 h-full flex items-center gap-2">
                <button
                    onClick={() => { setEditorOpen(true); }}
                    className="p-2 bg-zinc-800 hover:bg-zinc-700 hover:text-white rounded-lg text-zinc-400 transition-colors border border-zinc-700/50 shadow-sm"
                    title={skill.can_manage ? t("skillDetail.editClone") : t("skills.clone")}
                >
                    <RefreshCw className="w-5 h-5" />
                </button>

                {skill.can_manage && (
                    <button
                        onClick={() => setShowDeleteConfirm(true)}
                        className="p-2 bg-red-950/30 hover:bg-red-900/50 text-red-400 hover:text-red-300 rounded-lg transition-colors border border-red-900/30"
                        title={t("skillDetail.deleteSkill")}
                    >
                        <Trash2 className="w-5 h-5" />
                    </button>
                )}
             </div>
          </div>
        </div>
      </div>

      {/* Main Content - Left/Right Split */}
      <div className="grid grid-cols-1 lg:grid-cols-5 gap-6 min-h-[500px] lg:h-[750px]">

        {/* Left Column (40%): Meta + File List */}
        <div className="lg:col-span-2 flex flex-col gap-6 h-full overflow-hidden">

          {/* Description */}
          <div className="shrink-0 bg-zinc-900/30 border border-zinc-800 rounded-xl overflow-hidden flex flex-col min-h-[160px] max-h-[260px]">
             <div className="px-5 py-3 border-b border-zinc-800 bg-zinc-900/50 flex items-center justify-between">
               <div className="flex items-center gap-2">
                 <Dna className="w-4 h-4 text-zinc-400" />
                 <h3 className="text-sm font-semibold text-zinc-200">{t("skillDetail.description")}</h3>
               </div>
               <button
                 onClick={() => copyToClipboard(skill.description, setCopiedDesc)}
                 className="text-zinc-500 hover:text-zinc-200 transition-colors"
               >
                 {copiedDesc ? <Check className="w-3.5 h-3.5 text-green-500" /> : <Copy className="w-3.5 h-3.5" />}
               </button>
             </div>
             <div className="p-5 overflow-auto custom-scrollbar min-h-[60px]">
                {skill.description.trim() ? (
                  <RenderMarkdown content={skill.description} components={magnusLinkComponents} />
                ) : (
                  <p className="text-sm text-zinc-600 italic">{t("skillDetail.noDescription")}</p>
                )}
             </div>
          </div>

          {/* File List */}
          <div className="flex-1 min-h-0 bg-zinc-900/30 border border-zinc-800 rounded-xl overflow-hidden flex flex-col">
            <div className="shrink-0 px-5 py-3 border-b border-zinc-800 bg-zinc-900/50 flex items-center gap-2">
              <FileText className="w-4 h-4 text-zinc-400" />
              <h3 className="text-sm font-semibold text-zinc-200">{t("skillDetail.files")}</h3>
              <span className="text-xs text-zinc-600 ml-auto">{sortedFiles.length}</span>
            </div>
            <div className="flex-1 overflow-auto">
              {sortedFiles.length === 0 ? (
                <div className="p-5 text-zinc-500 text-sm">{t("skillDetail.noFiles")}</div>
              ) : (
                <div className="py-1">
                  {sortedFiles.map((file) => (
                    <button
                      key={file.path}
                      onClick={() => setActiveFile(file)}
                      className={`w-full text-left px-5 py-2.5 flex items-center gap-3 text-sm transition-colors ${
                        activeFile?.path === file.path
                          ? "bg-blue-600/10 text-blue-400 border-l-2 border-blue-500"
                          : "text-zinc-400 hover:bg-zinc-800/50 border-l-2 border-transparent"
                      }`}
                    >
                      {file.is_binary
                        ? <ImageIcon className="w-3.5 h-3.5 flex-shrink-0" />
                        : <FileText className="w-3.5 h-3.5 flex-shrink-0" />
                      }
                      <span className="truncate font-mono text-xs">{file.path}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Right Column (60%): File Content */}
        <div className="lg:col-span-3 h-full flex flex-col bg-[#0c0c0e] border border-zinc-800 rounded-xl overflow-hidden shadow-2xl">
          {activeFile ? (
            <>
              <div className="shrink-0 px-5 py-3 border-b border-zinc-800 bg-zinc-900/50 flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <FileText className="w-4 h-4 text-blue-400" />
                  <h3 className="text-sm font-semibold text-zinc-200 font-mono">{activeFile.path}</h3>
                </div>
                <button
                  onClick={() => copyToClipboard(activeFile.content, setCopiedFile)}
                  className={`text-zinc-500 hover:text-zinc-200 transition-colors ${isImage ? "hidden" : ""}`}
                  title="Copy content"
                >
                  {copiedFile ? <Check className="w-3.5 h-3.5 text-green-500" /> : <Copy className="w-3.5 h-3.5" />}
                </button>
              </div>
              <div className="flex-1 overflow-auto p-5">
                {isImage ? (
                  <div className="flex items-center justify-center min-h-full">
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={`/api/skills/${skillId}/files/${activeFile.path}?token=${typeof window !== "undefined" ? localStorage.getItem("magnus_token") || "" : ""}`}
                      alt={activeFile.path}
                      className="rounded-md border border-zinc-800 bg-zinc-950 max-w-full max-h-[650px] object-contain"
                    />
                  </div>
                ) : isMarkdown ? (
                  <RenderMarkdown content={activeFile.content} components={magnusLinkComponents} />
                ) : (
                  <div className="bg-[#1e1e1e] rounded-lg overflow-hidden min-h-full">
                    <CodeEditor
                      value={activeFile.content}
                      readOnly
                      filename={activeFile.path}
                    />
                  </div>
                )}
              </div>
            </>
          ) : (
            <div className="flex-1 flex items-center justify-center text-zinc-600 text-sm">
              {t("skillDetail.noFiles")}
            </div>
          )}
        </div>
      </div>

      {/* Dialogs */}
      <SkillEditor
        isOpen={editorOpen}
        mode="clone"
        initialData={editorData}
        initialResources={skill?.files.filter(f => f.is_binary).map(f => ({ path: f.path })) || []}
        onClose={() => { setEditorOpen(false); fetchSkill(true); }}
        onSave={handleEditorSave}
      />

      <ConfirmationDialog
        isOpen={showDeleteConfirm}
        onClose={() => setShowDeleteConfirm(false)}
        onConfirm={handleDelete}
        title={t("skillDetail.deleteSkill")}
        description={<span>{t("skillDetail.deleteConfirmDesc", { title: skill.title })}</span>}
        confirmText={t("skillDetail.deleteSkill")}
        variant="danger"
        isLoading={isDeleting}
        confirmInput={skill.id}
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
