// front_end/src/app/(main)/enchant/page.tsx
"use client";

import { useRouter } from "next/navigation";
import { Plus, ArrowDown } from "lucide-react";
import { client } from "@/lib/api";
import type { EnchantSession } from "@/types/enchant";


export default function EnchantPage() {
  const router = useRouter();


  const createSession = async () => {
    try {
      const newSession: EnchantSession = await client("/api/enchant/sessions", {
        json: { title: "New Session" },
      });
      window.dispatchEvent(new Event("enchant-sessions-update"));
      router.push(`/enchant/${newSession.id}`);
    } catch (error) {
      console.error("Failed to create session:", error);
    }
  };

  return (
    <div className="flex-1 flex flex-col items-center justify-center text-zinc-500">
      <div className="p-6 rounded-full bg-zinc-900/50 border border-zinc-800/50 mb-6">
        <ArrowDown className="w-12 h-12 text-zinc-600" strokeWidth={1.5} />
      </div>
      <h2 className="text-xl font-medium text-zinc-300 mb-6">Enchant</h2>
      <button
        onClick={createSession}
        className="flex items-center gap-2 px-6 py-3 bg-blue-600 hover:bg-blue-500 text-white rounded-lg text-sm font-medium transition-colors shadow-lg shadow-blue-900/20 active:scale-95 border border-blue-500/50"
      >
        <Plus className="w-4 h-4" />
        <span>New Session</span>
      </button>
    </div>
  );
}
