// front_end/src/components/layout/language-toggle.tsx
"use client";

import { Globe, Check } from "lucide-react";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { useLanguage, Language } from "@/context/language-context";


const LANGUAGE_OPTIONS: { value: Language; label: string; nativeLabel: string }[] = [
  { value: "zh", label: "Chinese", nativeLabel: "简体中文" },
  { value: "en", label: "English", nativeLabel: "English" },
];


export function LanguageToggle() {
  const { language, setLanguage } = useLanguage();

  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>
        <button
          className="p-2 rounded-lg transition-all text-zinc-400 hover:text-white hover:bg-zinc-800/50 data-[state=open]:bg-zinc-800 data-[state=open]:text-white focus:outline-none"
        >
          <Globe className="w-4 h-4" />
        </button>
      </DropdownMenu.Trigger>

      <DropdownMenu.Portal>
        <DropdownMenu.Content
          align="end"
          sideOffset={8}
          className="w-40 bg-[#0A0A0C] border border-zinc-800 rounded-xl shadow-2xl z-[200] overflow-hidden ring-1 ring-white/5 animate-in fade-in zoom-in-95 duration-100"
        >
          <div className="py-1">
            {LANGUAGE_OPTIONS.map((option) => (
              <DropdownMenu.Item
                key={option.value}
                onSelect={() => setLanguage(option.value)}
                className={`w-full px-3 py-2 text-left text-sm flex items-center justify-between transition-colors cursor-pointer focus:outline-none ${
                  language === option.value
                    ? "bg-zinc-800/50 text-zinc-100"
                    : "text-zinc-400 hover:bg-zinc-800/30 hover:text-zinc-200 focus:bg-zinc-800/30 focus:text-zinc-200"
                }`}
              >
                <span>{option.nativeLabel}</span>
                {language === option.value && (
                  <Check className="w-4 h-4 text-blue-400" />
                )}
              </DropdownMenu.Item>
            ))}
          </div>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}
