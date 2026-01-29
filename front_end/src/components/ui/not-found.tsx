// front_end/src/components/ui/not-found.tsx
"use client";

import { FileQuestion } from "lucide-react";
import { ArrowLeft } from "lucide-react";

interface NotFoundProps {
  title: string;
  description: string;
  buttonText: string;
  onBack: () => void;
}

export function NotFound({ title, description, buttonText, onBack }: NotFoundProps) {
  return (
    <div className="flex flex-col items-center justify-center h-[60vh] text-zinc-400 gap-6">
      <div className="bg-zinc-900/50 p-8 rounded-2xl border border-zinc-800 text-center max-w-md shadow-2xl backdrop-blur-sm">
        <div className="w-16 h-16 bg-zinc-800/80 rounded-full flex items-center justify-center mx-auto mb-6 border border-zinc-700/50 shadow-inner">
          <FileQuestion className="w-8 h-8 text-zinc-500" />
        </div>
        <h2 className="text-xl font-bold text-zinc-200 mb-2 tracking-tight">{title}</h2>
        <p className="text-zinc-500 text-sm mb-8 leading-relaxed">{description}</p>
        <button
          onClick={onBack}
          className="px-6 py-2.5 bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium rounded-lg transition-all shadow-lg shadow-blue-900/20 active:scale-95 flex items-center justify-center gap-2 mx-auto"
        >
          <ArrowLeft className="w-4 h-4" /> {buttonText}
        </button>
      </div>
    </div>
  );
}
