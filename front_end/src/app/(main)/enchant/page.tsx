// front_end/src/app/(main)/enchant/page.tsx
"use client";

import { ArrowRight, Construction } from "lucide-react";

export default function EnchantPage() {
  return (
    <div className="flex flex-col items-center justify-center h-full w-full min-h-[calc(100vh-4rem)] text-zinc-500 animate-in fade-in duration-500">
      <style jsx global>{`
        ::-webkit-scrollbar { display: none; }
        html { -ms-overflow-style: none; scrollbar-width: none; }
      `}</style>
      <div className="p-6 rounded-full bg-zinc-900/50 border border-zinc-800/50 mb-6">
        <ArrowRight className="w-12 h-12 text-zinc-600" strokeWidth={1.5} />
      </div>
      <h2 className="text-xl font-semibold text-zinc-300 mb-2">Enchant</h2>
      <div className="flex items-center gap-2 text-sm text-zinc-600 bg-zinc-900/30 px-3 py-1.5 rounded-full border border-zinc-800/30">
        <Construction className="w-3.5 h-3.5" />
        <span>Module Under Construction</span>
      </div>
    </div>
  );
}
