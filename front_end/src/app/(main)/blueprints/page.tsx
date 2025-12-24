// front_end/src/app/(main)/blueprints/page.tsx
"use client";

import { useState, useEffect, useCallback, useMemo } from "react";
import { useRouter } from "next/navigation";
import { 
  Play, X, Search, Loader2, Terminal, 
  RefreshCw, Trash2, FileCode, DraftingCompass, Plus
} from "lucide-react";
import { client } from "@/lib/api";
import { cn, formatBeijingTime } from "@/lib/utils";
import { useAuth } from "@/context/auth-context";
import { PaginationControls } from "@/components/ui/pagination-controls";
import { CopyableText } from "@/components/ui/copyable-text";
import { UserAvatar } from "@/components/ui/user-avatar";
import { NumberStepper } from "@/components/ui/number-stepper";
import { SearchableSelect } from "@/components/ui/searchable-select";
import { ConfirmationDialog } from "@/components/ui/confirmation-dialog";
import { POLL_INTERVAL } from "@/lib/config";

import Editor from "react-simple-code-editor";
import { highlight, languages } from "prismjs";
import "prismjs/components/prism-clike";
import "prismjs/components/prism-python";
import "prismjs/themes/prism-okaidia.css";

interface BlueprintParam {
  key: string;
  label: string;
  type: 'text' | 'number' | 'select' | 'boolean';
  default?: any;
  options?: string[];
  description?: string;
  min?: number;
  max?: number;
}

interface Blueprint {
  id: string;
  title: string;
  description: string;
  code: string;
  user_id: string;
  user?: { 
    id: string;
    name: string;
    avatar_url?: string;
    email?: string;
  }; 
  updatedAt: string;
}

interface User {
  id: string;
  name: string;
  avatar_url?: string;
  email?: string;
}

const DEFAULT_CODE_TEMPLATE = `from typing import Annotated

def generate_job(
    user_name: str,
    gpu_count: Annotated[int, {"min": 1, "max": 8, "label": "GPU Count"}] = 1,
    task_suffix: Annotated[str, {"description": "Suffix for task name"}] = "demo"
) -> JobSubmission:
    
    return JobSubmission(
        task_name=f"BP-{task_suffix}",
        description=f"Created by {user_name}",
        namespace="PKU-Plasma",
        repo_name="magnus",
        branch="main",
        commit_sha="HEAD",
        entry_command="python back_end/python_scripts/magnus_debug.py",
        gpu_count=gpu_count,
        gpu_type="rtx5090",
        job_type=JobType.A2
    )
`;

interface BlueprintDrawerProps {
    isOpen: boolean;
    mode: 'create' | 'clone';
    initialData: { id: string; title: string; description: string; code: string };
    onClose: () => void;
    onSave: (data: any) => Promise<void>;
    isSaving: boolean;
}

function BlueprintDrawer({ isOpen, mode, initialData, onClose, onSave, isSaving }: BlueprintDrawerProps) {
    const [formData, setFormData] = useState(initialData);
    const [errorField, setErrorField] = useState<string | null>(null);
    const [errorMessage, setErrorMessage] = useState<string | null>(null);
    const handleKeyDown = (e: React.KeyboardEvent) => {
        const target = e.currentTarget as HTMLTextAreaElement;
        const { value, selectionStart, selectionEnd } = target;
        if (e.key === 'Tab') {
            e.preventDefault();
            const newValue = value.substring(0, selectionStart) + "    " + value.substring(selectionEnd);
            setFormData(prev => ({ ...prev, code: newValue }));
            setTimeout(() => {
                target.selectionStart = target.selectionEnd = selectionStart + 4;
            }, 0);
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
                } else {
                    newValue = value; 
                }
            } else {
                const match = currentLine.match(/^(\s*)(.*)$/);
                const indent = match ? match[1] : "";
                const content = match ? match[2] : currentLine;
                const commentedLine = indent + "# " + content;
                newValue = value.substring(0, lineStart) + commentedLine + value.substring(lineEnd);
                newCursorPos = selectionStart + 2;
            }

            setFormData(prev => ({ ...prev, code: newValue }));
            
            setTimeout(() => {
                target.selectionStart = target.selectionEnd = newCursorPos;
            }, 0);
        }
    };

    useEffect(() => {
        if (isOpen) {
            setFormData(initialData);
            setErrorField(null);
            setErrorMessage(null);
        }
    }, [isOpen, initialData]);

    useEffect(() => {
        const handleEsc = (e: KeyboardEvent) => {
            if (e.key === "Escape" && isOpen) onClose();
        };
        window.addEventListener("keydown", handleEsc);
        return () => window.removeEventListener("keydown", handleEsc);
    }, [isOpen, onClose]);

    const scrollToError = (id: string) => {
        const el = document.getElementById(id);
        if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    };

    const clearError = (field: string) => {
        if (errorField === field) { setErrorField(null); setErrorMessage(null); }
    };

    const handleSubmit = () => {
        const id = formData.id.trim();
        const title = formData.title.trim();
        const description = formData.description.trim();

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
    };

    return (
        <>
            <div 
                className={cn(
                    "fixed inset-0 bg-black/60 backdrop-blur-sm z-[90] transition-opacity duration-300",
                    isOpen ? "opacity-100 pointer-events-auto" : "opacity-0 pointer-events-none"
                )}
                onClick={onClose}
            />

            <div className={cn(
                "fixed top-0 right-0 h-full w-full max-w-4xl bg-[#09090b] border-l border-zinc-800 shadow-2xl z-[100] transform transition-transform duration-300 ease-in-out flex flex-col",
                isOpen ? "translate-x-0" : "translate-x-full"
            )}>
                <div className="px-6 py-5 border-b border-zinc-800 flex items-center justify-between bg-zinc-900/50 backdrop-blur-sm flex-shrink-0">
                    <div>
                        <h2 className="text-lg font-bold text-white flex items-center gap-2">
                            {mode === 'create' ? <DraftingCompass className="w-5 h-5 text-blue-500"/> : <RefreshCw className="w-5 h-5 text-purple-500"/>}
                            {mode === 'create' ? "Create Blueprint" : "Clone Blueprint"}
                        </h2>
                    </div>
                    <button 
                        onClick={onClose} 
                        className="text-zinc-500 hover:text-white transition-colors bg-zinc-800/50 hover:bg-zinc-700 p-1.5 rounded-md"
                    >
                        <X className="w-4 h-4" />
                    </button>
                </div>

                <div className="flex-1 overflow-y-auto p-6 custom-scrollbar">
                     <div className="flex flex-col gap-6 max-w-3xl mx-auto">
                        
                        <div className="space-y-6">
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
                                    onChange={e => { setFormData({...formData, title: e.target.value}); clearError('title'); }}
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
                                    onChange={e => { setFormData({...formData, id: e.target.value}); clearError('id'); }}
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
                                    onChange={e => { setFormData({...formData, description: e.target.value}); clearError('description'); }}
                                    placeholder="Brief description (single line)..."
                                    className={`w-full bg-zinc-950 border px-4 py-2.5 rounded-lg text-zinc-200 text-sm focus:border-blue-500 outline-none transition-all placeholder-zinc-700
                                        ${errorField === 'description' ? 'animate-shake border-red-500' : 'border-zinc-800'}`}
                                />
                            </div>
                        </div>

                        <div className="flex flex-col flex-1">
                            <h3 className="text-zinc-200 text-sm font-semibold mb-4 flex items-center gap-2">
                                Implementation
                                <div className="h-px bg-zinc-800 flex-grow ml-2"></div>
                            </h3>

                            <label className="text-xs uppercase font-bold text-zinc-500 mb-2 flex items-center gap-2">
                                <Terminal className="w-3 h-3"/> Python Logic
                            </label>
                            
                            <div className="relative rounded-xl overflow-hidden border border-zinc-800 bg-[#1e1e1e] focus-within:ring-1 focus-within:ring-blue-500/50 transition-all shadow-inner">
                                <Editor
                                    value={formData.code}
                                    onValueChange={code => setFormData({ ...formData, code })}
                                    highlight={code => highlight(code, languages.python, 'python')}
                                    padding={24}
                                    onKeyDown={handleKeyDown}
                                    className="prism-editor font-mono text-sm leading-relaxed"
                                    style={{
                                        fontFamily: '"Fira Code", "Fira Mono", monospace',
                                        fontSize: 14,
                                        backgroundColor: "transparent",
                                        minHeight: "400px",
                                    }}
                                    textareaClassName="focus:outline-none"
                                />
                            </div>
                        </div>

                        <div className="mt-2 pt-6 border-t border-zinc-800 flex flex-col-reverse sm:flex-row sm:justify-between sm:items-center gap-4">
                            {errorMessage ? (
                                <span className="text-red-500 text-xs font-bold animate-pulse text-center sm:text-left">{errorMessage}</span>
                            ) : (
                                <span className="text-zinc-500 text-xs text-center sm:text-left hidden sm:block">Waiting to be saved.</span>
                            )}
                            
                            <div className="flex gap-3 w-full sm:w-auto">
                                <button 
                                    onClick={onClose} 
                                    className="flex-1 sm:flex-none px-4 py-2.5 rounded-lg text-sm font-medium text-zinc-400 hover:text-white hover:bg-zinc-800 transition-colors"
                                >
                                    Cancel
                                </button>
                                <button 
                                    onClick={handleSubmit}
                                    disabled={isSaving}
                                    className="flex-1 sm:flex-none px-6 py-2.5 rounded-lg text-sm font-medium bg-blue-600 hover:bg-blue-500 text-white shadow-lg shadow-blue-900/20 active:scale-95 transition-all flex items-center justify-center gap-2"
                                >
                                    {isSaving ? <Loader2 className="w-4 h-4 animate-spin"/> : (mode === 'create' ? <DraftingCompass className="w-4 h-4" /> : <RefreshCw className="w-4 h-4" />)}
                                    {mode === 'create' ? "Create Blueprint" : "Clone Blueprint"}
                                </button>
                            </div>
                        </div>

                     </div>
                </div>
            </div>
        </>
    );
}

function BlueprintTable({ 
    data, 
    loading, 
    onRun, 
    onClone, 
    onDelete,
    emptyMessage = "No blueprints found."
}: { 
    data: Blueprint[], 
    loading: boolean, 
    onRun: (bp: Blueprint) => void,
    onClone: (bp: Blueprint) => void,
    onDelete: (bp: Blueprint) => void,
    emptyMessage?: string
}) {
    const { user: currentUser } = useAuth();

    if (loading) {
        return (
            <div className="border border-zinc-800 rounded-xl bg-zinc-900/30 shadow-sm flex flex-col items-center justify-center text-zinc-500 gap-3 min-h-[400px]">
                <Loader2 className="w-8 h-8 animate-spin text-blue-500" />
                <p className="text-sm font-medium">Fetching blueprints...</p>
            </div>
        );
    }

    if (data.length === 0) {
        return (
            <div className="border border-zinc-800 rounded-xl bg-zinc-900/30 shadow-sm flex flex-col items-center justify-center text-zinc-500 min-h-[400px]">
                <FileCode className="w-10 h-10 opacity-20 mb-3" />
                <p className="text-base font-medium text-zinc-400">{emptyMessage}</p>
            </div>
        );
    }

    return (
        <div className="border border-zinc-800 rounded-xl bg-zinc-900/30 shadow-sm flex flex-col overflow-hidden min-h-[400px]">
            <div className="overflow-x-auto w-full">
                <table className="w-full text-left text-sm whitespace-nowrap table-fixed">
                    <thead className="bg-zinc-900/90 text-zinc-500 border-b border-zinc-800 backdrop-blur-md">
                        <tr>
                            <th className="px-6 py-4 font-medium w-[25%]">Blueprint / Blueprint ID</th>
                            <th className="px-6 py-4 font-medium w-[45%]">Description</th>
                            <th className="px-6 py-4 font-medium w-[15%] text-center">Creator / Updated at</th>
                            <th className="px-6 py-4 font-medium text-right w-[15%]"></th>
                        </tr>
                    </thead>
                    <tbody className="divide-y divide-zinc-800/50">
                        {data.map((bp) => {
                            const isOwner = currentUser?.id === bp.user_id;

                            return (
                                <tr 
                                    key={bp.id} 
                                    onClick={() => onRun(bp)} 
                                    className="hover:bg-zinc-800/40 transition-colors group border-b border-zinc-800/50 last:border-0 cursor-pointer"
                                >
                                    <td className="px-6 py-4 align-top whitespace-normal break-all">
                                        <div className="flex flex-col gap-1.5">
                                            <div className="flex items-center gap-2">
                                                <CopyableText 
                                                    text={bp.title} 
                                                    variant="text" 
                                                    className="font-semibold text-zinc-200 text-base"
                                                />
                                            </div>
                                            <div className="flex items-center gap-2">
                                                <CopyableText text={bp.id} className="text-[10px] tracking-wider" />
                                            </div>
                                        </div>
                                    </td>

                                    <td className="px-6 py-4 align-top whitespace-normal">
                                        <p className="text-zinc-400 text-sm leading-relaxed line-clamp-2">
                                            {bp.description}
                                        </p>
                                    </td>

                                    <td className="px-6 py-4 align-top">
                                        <div className="flex justify-center">
                                            <UserAvatar 
                                                user={bp.user || { id: bp.user_id, name: "Unknown" }} 
                                                subText={formatBeijingTime(bp.updatedAt)} 
                                            />
                                        </div>
                                    </td>

                                    <td className="px-6 py-4 align-middle text-right">
                                        <div className="flex justify-end gap-2 opacity-0 group-hover:opacity-100 transition-all transform translate-x-2 group-hover:translate-x-0">
                                            <button
                                                onClick={(e) => { e.stopPropagation(); onClone(bp); }}
                                                className="p-2 bg-zinc-800 hover:bg-zinc-700 hover:text-white rounded-lg text-zinc-400 transition-colors border border-zinc-700/50 shadow-sm"
                                                title="Clone Blueprint"
                                            >
                                                <RefreshCw className="w-4 h-4" />
                                            </button>

                                            <button
                                                onClick={(e) => { e.stopPropagation(); onRun(bp); }}
                                                className="p-2 bg-blue-900/20 hover:bg-blue-600 hover:text-white text-blue-500 rounded-lg transition-colors border border-blue-500/20 shadow-sm"
                                                title="Run Blueprint"
                                            >
                                                <Play className="w-4 h-4 fill-current" />
                                            </button>

                                            {isOwner && (
                                                <button
                                                    onClick={(e) => { e.stopPropagation(); onDelete(bp); }}
                                                    className="p-2 bg-red-950/30 hover:bg-red-900/50 text-red-400 hover:text-red-300 rounded-lg transition-colors border border-red-900/30"
                                                    title="Delete Blueprint"
                                                >
                                                    <Trash2 className="w-4 h-4" />
                                                </button>
                                            )}
                                        </div>
                                    </td>
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
            </div>
        </div>
    );
}

export default function BlueprintsPage() {
  const router = useRouter();
  const { user: currentUser } = useAuth();
  
  const [isDrawerOpen, setIsDrawerOpen] = useState(false);
  const [drawerMode, setDrawerMode] = useState<'create' | 'clone'>('create');
  
  const [blueprints, setBlueprints] = useState<Blueprint[]>([]);
  const [allUsers, setAllUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  
  const [searchQuery, setSearchQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [selectedUserId, setSelectedUserId] = useState("");

  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [totalItems, setTotalItems] = useState(0);

  const [selectedBlueprint, setSelectedBlueprint] = useState<Blueprint | null>(null);
  const [paramsSchema, setParamsSchema] = useState<BlueprintParam[]>([]);
  const [formValues, setFormValues] = useState<Record<string, any>>({});
  const [isRunning, setIsRunning] = useState(false);
  const [isLoadingSchema, setIsLoadingSchema] = useState(false);

  const [blueprintToDelete, setBlueprintToDelete] = useState<Blueprint | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);

  const [editorData, setEditorData] = useState({
    id: "",
    title: "",
    description: "",
    code: DEFAULT_CODE_TEMPLATE
  });
  const [isSaving, setIsSaving] = useState(false);

  useEffect(() => {
    const fetchUsers = async () => {
      try {
        const users = await client("/api/users");
        setAllUsers(users);
      } catch (e) { console.error(e); }
    };
    fetchUsers();
  }, []);

  const userFilterOptions = useMemo(() => {
    return [
      { label: "All Users", value: "", icon: "/api/logo" },
      ...allUsers.map(u => ({ label: u.name, value: u.id, meta: u.email || "", icon: u.avatar_url }))
    ];
  }, [allUsers]);

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedQuery(searchQuery), 300);
    return () => clearTimeout(timer);
  }, [searchQuery]);

  useEffect(() => { setCurrentPage(1); }, [debouncedQuery, selectedUserId]);

  const fetchBlueprints = useCallback(async (isBackground = false) => {
    if (!isBackground) setLoading(true);
    try {
      const skip = (currentPage - 1) * pageSize;
      const params = new URLSearchParams({
          skip: skip.toString(),
          limit: pageSize.toString(),
      });
      if (debouncedQuery.trim()) params.append("search", debouncedQuery.trim());
      if (selectedUserId) params.append("creator_id", selectedUserId);
      const response = await client(`/api/blueprints?${params.toString()}`);
      setBlueprints(response.items.map((b: any) => ({ 
          ...b, 
          updatedAt: b.updated_at || b.updatedAt,
          user: b.user || allUsers.find(u => u.id === b.user_id) 
      })));
      setTotalItems(response.total);
    } catch (e) {
      console.error(e);
    } finally {
      if (!isBackground) setLoading(false);
    }
  }, [currentPage, pageSize, debouncedQuery, selectedUserId, allUsers]);

  useEffect(() => { fetchBlueprints(); }, [fetchBlueprints]);

  const handleOpenRunDialog = async (bp: Blueprint) => {
    setSelectedBlueprint(bp);
    setIsLoadingSchema(true);
    setParamsSchema([]);
    try {
      const schema = await client(`/api/blueprints/${bp.id}/schema`);
      setParamsSchema(schema);
      const initial: Record<string, any> = {};
      schema.forEach((p: BlueprintParam) => { initial[p.key] = p.default ?? ""; });
      setFormValues(initial);
    } catch (e) {
      alert("Failed to parse blueprint schema.");
      setSelectedBlueprint(null);
    } finally {
      setIsLoadingSchema(false);
    }
  };

  const handleRun = async () => {
    if (!selectedBlueprint) return;
    setIsRunning(true);
    try {
      await client(`/api/blueprints/${selectedBlueprint.id}/run`, { method: "POST", json: formValues });
      router.push('/jobs');
    } catch (e: any) {
      alert(`Failed to start task: ${e.message}`);
      setIsRunning(false);
    }
  };

  const handleClone = (bp: Blueprint) => {
      setEditorData({
          id: `${bp.id}-copy`,
          title: `Copy of ${bp.title}`,
          description: bp.description,
          code: bp.code
      });
      setDrawerMode('clone');
      setIsDrawerOpen(true);
  };

  const handleClickDelete = (bp: Blueprint) => {
      setBlueprintToDelete(bp);
  };

  const executeDelete = async () => {
    if (!blueprintToDelete) return;
    setIsDeleting(true);
    try {
        await client(`/api/blueprints/${blueprintToDelete.id}`, { method: "DELETE" });
        fetchBlueprints();
        setBlueprintToDelete(null);
    } catch (e: any) {
        alert(`Delete failed: ${e.message}`);
    } finally {
        setIsDeleting(false);
    }
  };

  const handleSaveBlueprint = async (data: any) => {
    setIsSaving(true);
    try {
        await client("/api/blueprints", { method: "POST", json: data });
        setIsDrawerOpen(false);
        fetchBlueprints();
    } catch (e: any) {
        alert(`Save failed: ${e.message}`);
    } finally {
        setIsSaving(false);
    }
  };

  return (
    <div className="relative min-h-[calc(100vh-8rem)] pb-20">

      <style jsx global>{`
          .prism-editor textarea { outline: none !important; }
          code[class*="language-"], pre[class*="language-"] {
              text-shadow: none !important;
              font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace !important;
          }
          ::-webkit-scrollbar { display: none; }
          html { -ms-overflow-style: none; scrollbar-width: none; }
      `}</style>

      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-white tracking-tight flex items-center gap-2">
            Blueprints Registry
          </h1>
          <p className="text-zinc-500 text-sm mt-1">
            Standardized task templates via Python-defined logic.
          </p>
        </div>
        <button 
            onClick={() => {
                setEditorData({ id: "", title: "", description: "", code: DEFAULT_CODE_TEMPLATE });
                setDrawerMode('create');
                setIsDrawerOpen(true);
            }}
            className="bg-blue-600 hover:bg-blue-500 text-white px-4 py-2 rounded-lg text-sm font-medium flex items-center gap-2 transition-colors shadow-lg shadow-blue-900/20 active:scale-95 border border-blue-500/50"
        >
            <DraftingCompass className="w-4 h-4"/> New Blueprint
        </button>
      </div>

      {/* Filters */}
      <div className="bg-zinc-900/40 border border-zinc-800 rounded-xl p-1.5 mb-6 flex items-center gap-2 backdrop-blur-sm relative z-20">
        <div className="relative flex-1 group">
           <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-zinc-500 group-focus-within:text-blue-500 transition-colors" />
           <input 
             type="text" 
             value={searchQuery}
             onChange={(e) => setSearchQuery(e.target.value)}
             placeholder="Search Blueprints..." 
             className="w-full bg-transparent border-none py-2.5 pl-9 pr-4 text-sm text-zinc-200 focus:outline-none focus:ring-0 placeholder-zinc-600"
           />
        </div>
        <div className="h-6 w-px bg-zinc-800"></div>
        <div className="w-56"> 
          <SearchableSelect
             value={selectedUserId}
             onChange={setSelectedUserId}
             options={userFilterOptions}
             placeholder="Filter by User"
             className="mb-0 border-none bg-transparent" 
          />
        </div>
      </div>

      {/* Table Area */}
      <div className="bg-zinc-900/40 border border-zinc-800 rounded-xl overflow-hidden backdrop-blur-sm">
        <BlueprintTable 
            data={blueprints} 
            loading={loading} 
            onRun={handleOpenRunDialog}
            onClone={handleClone}
            onDelete={handleClickDelete}
        />

        {/* Pagination */}
        {blueprints.length > 0 && (
          <div className="px-6 py-2 border-t border-zinc-800/30">
            <PaginationControls 
              currentPage={currentPage}
              totalPages={Math.ceil(totalItems / pageSize)}
              pageSize={pageSize}
              totalItems={totalItems}
              onPageChange={setCurrentPage}
              onPageSizeChange={(newSize) => {
                 setPageSize(newSize);
                 setCurrentPage(1);
              }}
            />
          </div>
        )}
      </div>

      {/* Drawer */}
      <BlueprintDrawer 
          isOpen={isDrawerOpen} 
          mode={drawerMode}
          initialData={editorData}
          onClose={() => setIsDrawerOpen(false)}
          onSave={handleSaveBlueprint}
          isSaving={isSaving}
      />

      {/* Confirmation Dialog */}
      <ConfirmationDialog
        isOpen={!!blueprintToDelete}
        onClose={() => setBlueprintToDelete(null)}
        onConfirm={executeDelete}
        title="Delete Blueprint"
        description={blueprintToDelete ? (
            <span>
                Are you sure you want to delete blueprint <strong>{blueprintToDelete.title}</strong>?
                <br />
                This action cannot be undone.
            </span>
        ) : null}
        confirmText="Delete"
        variant="danger"
        isLoading={isDeleting}
      />

      {/* Run Dialog */}
      {selectedBlueprint && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm animate-in fade-in duration-200">
          <div className="bg-[#0c0c0e] border border-zinc-800 rounded-2xl shadow-2xl w-full max-w-lg overflow-hidden animate-in zoom-in-95 duration-200 flex flex-col max-h-[90vh]">
            <div className="px-6 py-4 border-b border-zinc-800 bg-zinc-900/30 flex items-center justify-between flex-shrink-0">
              <div className="flex items-center gap-3">
                <div className="p-2 rounded-lg bg-blue-900/20 border border-blue-500/30 text-blue-500">
                  <Terminal className="w-5 h-5" />
                </div>
                <div>
                  <h3 className="text-lg font-bold text-zinc-100">{selectedBlueprint.title}</h3>
                  <p className="text-xs text-zinc-500">Configure parameters</p>
                </div>
              </div>
              <button onClick={() => setSelectedBlueprint(null)} className="text-zinc-500 hover:text-zinc-300 transition-colors">
                <X className="w-5 h-5" />
              </button>
            </div>
            
            <div className="p-6 space-y-5 overflow-y-auto custom-scrollbar">
               {isLoadingSchema ? (
                   <div className="py-10 flex justify-center"><Loader2 className="w-6 h-6 animate-spin text-zinc-600"/></div>
               ) : paramsSchema.length === 0 ? (
                   <div className="text-center text-zinc-500 py-4">No parameters required.</div>
               ) : (
                   paramsSchema.map((param) => (
                    <div key={param.key} className="space-y-1.5">
                      <label className="text-xs uppercase tracking-wider mb-1.5 block font-medium text-zinc-500">
                        {param.label || param.key}
                      </label>
                      {param.type === 'number' ? (
                          <NumberStepper
                            label="" 
                            value={Number(formValues[param.key])}
                            onChange={(val) => setFormValues({...formValues, [param.key]: val})}
                            min={param.min ?? 0}
                            max={param.max ?? 128}
                          />
                      ) : param.type === 'select' ? (
                        <div className="relative">
                          <select
                            value={formValues[param.key]}
                            onChange={(e) => setFormValues({...formValues, [param.key]: e.target.value})}
                            className="w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-2.5 text-sm text-zinc-200 focus:outline-none focus:border-blue-500 transition-all appearance-none"
                          >
                            {param.options?.map(opt => <option key={opt} value={opt}>{opt}</option>)}
                          </select>
                           <div className="absolute right-3 top-3 pointer-events-none text-zinc-500"><Terminal className="w-4 h-4" /></div>
                        </div>
                      ) : param.type === 'boolean' ? (
                        <select
                            value={String(formValues[param.key])}
                            onChange={(e) => setFormValues({...formValues, [param.key]: e.target.value === 'true'})}
                            className="w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-2.5 text-sm text-zinc-200 focus:outline-none focus:border-blue-500 transition-all appearance-none"
                          >
                            <option value="true">True</option>
                            <option value="false">False</option>
                          </select>
                      ) : (
                        <input
                          type="text"
                          value={formValues[param.key]}
                          onChange={(e) => setFormValues({...formValues, [param.key]: e.target.value})}
                          className="w-full bg-zinc-950 border border-zinc-800 px-3 py-2.5 rounded-lg text-white text-sm focus:border-blue-500 outline-none transition-all placeholder-zinc-700"
                        />
                      )}
                      {param.description && <p className="text-[11px] text-zinc-500 mt-1 ml-0.5">{param.description}</p>}
                    </div>
                  ))
               )}
            </div>

            <div className="px-6 py-4 bg-zinc-900/50 border-t border-zinc-800 flex justify-end gap-3 flex-shrink-0">
              <button onClick={() => setSelectedBlueprint(null)} disabled={isRunning} className="px-4 py-2 text-sm font-medium text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800 rounded-lg transition-colors">Cancel</button>
              <button onClick={handleRun} disabled={isRunning || isLoadingSchema} className="flex items-center gap-2 px-5 py-2 text-sm font-bold text-white bg-blue-600 hover:bg-blue-500 rounded-lg shadow-lg shadow-blue-500/20 transition-all active:scale-95 disabled:opacity-50 disabled:cursor-not-allowed">
                {isRunning ? <><Loader2 className="w-4 h-4 animate-spin" /> Launching...</> : <><Play className="w-4 h-4 fill-current" /> Launch</>}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}