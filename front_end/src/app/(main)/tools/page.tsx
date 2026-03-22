// front_end/src/app/tools/page.tsx
"use client";

import { Wrench, Construction } from "lucide-react";

export default function ToolsPage() {
  return (
    <div className="flex-1 flex flex-col items-center justify-center px-4 py-8 text-zinc-500 animate-in fade-in duration-500">
      <style jsx global>{`
          .prism-editor textarea { outline: none !important; }
          code[class*="language-"], pre[class*="language-"] { text-shadow: none !important; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace !important; }
          ::-webkit-scrollbar { display: none; }
          html { -ms-overflow-style: none; scrollbar-width: none; }
      `}</style>
      <div className="p-6 rounded-full bg-zinc-900/50 border border-zinc-800/50 mb-6">
        <Wrench className="w-12 h-12 text-zinc-600" />
      </div>
      <h2 className="text-xl font-semibold text-zinc-300 mb-2">Tools</h2>
      <div className="flex items-center gap-2 text-sm text-zinc-600 bg-zinc-900/30 px-3 py-1.5 rounded-full border border-zinc-800/30">
        <Construction className="w-3.5 h-3.5" />
        <span>Module Under Construction</span>
      </div>
    </div>
  );
}