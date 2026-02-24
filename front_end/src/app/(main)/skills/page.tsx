// front_end/src/app/(main)/skills/page.tsx
"use client";

import { Dna, Construction } from "lucide-react";

export default function SkillsPage() {
  return (
    <div className="flex flex-col items-center justify-center h-full w-full min-h-[calc(100vh-4rem)] text-zinc-500 animate-in fade-in duration-500">
      <div className="p-6 rounded-full bg-zinc-900/50 border border-zinc-800/50 mb-6">
        <Dna className="w-12 h-12 text-zinc-600" />
      </div>
      <h2 className="text-xl font-semibold text-zinc-300 mb-2">Skills</h2>
      <p className="text-sm text-zinc-600 mb-4">Domain-specific knowledge modules for AI agents</p>
      <div className="flex items-center gap-2 text-sm text-zinc-600 bg-zinc-900/30 px-3 py-1.5 rounded-full border border-zinc-800/30">
        <Construction className="w-3.5 h-3.5" />
        <span>Module Under Construction</span>
      </div>
    </div>
  );
}
