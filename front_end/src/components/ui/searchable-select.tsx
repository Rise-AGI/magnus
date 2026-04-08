// front_end/src/components/ui/searchable-select.tsx
"use client";

import { useState, useEffect, useRef, useMemo } from "react";
import { X, Search, ChevronDown, ChevronUp } from "lucide-react";
import * as Popover from "@radix-ui/react-popover";

interface SearchableSelectProps {
  label?: string;
  value: string;
  options: { label: string; value: string; meta?: string; icon?: string; initials?: string }[];
  onChange: (val: string) => void;
  placeholder?: string;
  disabled?: boolean;
  hasError?: boolean;
  id?: string;
  className?: string;
  placement?: "top" | "bottom";
  minimal?: boolean;
}

export function SearchableSelect({
  label, value, options, onChange, placeholder, disabled, hasError, id, className = "",
  placement = "bottom",
  minimal = false
}: SearchableSelectProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [query, setQuery] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const triggerRef = useRef<HTMLDivElement>(null);
  const prevValueRef = useRef(value);

  useEffect(() => {
    const valueChanged = prevValueRef.current !== value;
    prevValueRef.current = value;

    // When options load but value is still empty, don't wipe the user's typed query
    if (!valueChanged && !value) return;

    const selectedOption = options.find(o => o.value === value);
    if (selectedOption) {
        setQuery(selectedOption.label);
    } else if (value) {
        setQuery(value);
    } else {
        setQuery("");
    }
  }, [value, options]);

  const handleOpenChange = (open: boolean) => {
    setIsOpen(open);
    if (!open) {
      const selectedOption = options.find(o => o.value === value);
      if (selectedOption) setQuery(selectedOption.label);
      else if (value) setQuery(value);
      else setQuery("");
    }
  };

  const handleClear = (e: React.MouseEvent) => {
    e.stopPropagation(); setQuery(""); onChange(""); inputRef.current?.focus();
  };

  const filteredOptions = useMemo(() => {
    if (query === "") return options;
    const selectedOption = options.find(o => o.value === value);
    if (selectedOption && query === selectedOption.label) return options;

    return options.filter((opt) => {
      const searchStr = (opt.label + (opt.meta || "")).toLowerCase();
      return searchStr.includes(query.toLowerCase());
    });
  }, [query, options, value]);

  const selectedOption = useMemo(() => options.find(o => o.value === value), [value, options]);
  const showIcon = !minimal && (selectedOption?.icon || selectedOption?.initials);

  return (
    <Popover.Root open={isOpen} onOpenChange={handleOpenChange}>
      <div className={`relative ${className}`} id={id}>
        {label && (
          <label className={`text-xs uppercase tracking-wider mb-1.5 block font-medium transition-colors ${hasError ? 'text-red-500' : 'text-zinc-500'}`}>
            {label} {hasError && "*"}
          </label>
        )}

        <Popover.Anchor asChild>
          <div className="relative group" ref={triggerRef}>

            {/* 选中项的图标 (绝对定位在输入框左侧) */}
            {showIcon && (
              <div className="absolute left-3 top-1/2 -translate-y-1/2 flex items-center pointer-events-none z-10">
                  {selectedOption?.icon ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={selectedOption.icon}
                      alt=""
                      className="w-6 h-6 rounded-full object-cover border border-zinc-700/50"
                    />
                  ) : (
                    <div className="w-6 h-6 rounded-full bg-indigo-500/20 text-indigo-400 flex items-center justify-center text-[10px] font-bold border border-indigo-500/30 overflow-hidden">
                      {selectedOption?.initials}
                    </div>
                  )}
              </div>
            )}

            <input
              ref={inputRef}
              type="text"
              disabled={disabled}
              className={`w-full bg-zinc-950 border px-3 py-2.5 rounded-lg text-sm text-white outline-none transition-all placeholder-zinc-600
                disabled:cursor-not-allowed disabled:text-zinc-500 disabled:bg-zinc-900/50
                ${minimal ? 'pr-6 text-center font-mono' : 'pr-10'}
                ${showIcon ? 'pl-10' : ''}
                ${hasError ? 'animate-shake border-red-500' : isOpen ? 'border-blue-500 ring-1 ring-blue-500/20' : 'border-zinc-800 hover:border-zinc-700'}
              `}
              placeholder={disabled ? "Waiting..." : (placeholder || "Search...")}
              value={query}
              onChange={(e) => { setQuery(e.target.value); setIsOpen(true); }}
              onFocus={() => !disabled && !minimal && setIsOpen(true)}
              onClick={() => minimal && !disabled && setIsOpen(!isOpen)}
            />

            {!minimal && (
              <div className="absolute right-3 top-0 h-full flex items-center gap-2">
                {!disabled && query && (
                  <button onClick={handleClear} className="p-0.5 text-zinc-500 hover:text-white rounded-full transition-colors">
                    <X className="w-3.5 h-3.5" />
                  </button>
                )}
                <div className="pointer-events-none text-zinc-600">
                  <Search className="w-3.5 h-3.5" />
                </div>
              </div>
            )}

            {minimal && !disabled && (
              <div className="absolute right-2 top-0 h-full flex items-center pointer-events-none text-zinc-600">
                  {placement === 'top' ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
              </div>
            )}
          </div>
        </Popover.Anchor>

        <Popover.Portal>
          <Popover.Content
            side={placement === "top" ? "top" : "bottom"}
            sideOffset={4}
            align="start"
            onOpenAutoFocus={(e) => e.preventDefault()}
            onInteractOutside={(e) => {
              if (triggerRef.current?.contains(e.target as Node)) e.preventDefault();
            }}
            style={{ width: triggerRef.current?.offsetWidth }}
            className="bg-[#0F0F11] border border-zinc-800 rounded-lg shadow-2xl z-[200] overflow-hidden max-h-60 overflow-y-auto custom-scrollbar animate-in fade-in duration-100"
          >
            {filteredOptions.map((opt) => (
              <div
                key={opt.value}
                onClick={() => { onChange(opt.value); setQuery(opt.label); setIsOpen(false); }}
                className={`px-3 py-2 cursor-pointer border-b border-zinc-800/50 last:border-0 hover:bg-blue-500/10 transition-colors flex items-center justify-between
                  ${opt.value === value ? 'bg-blue-500/20 text-blue-400' : 'text-zinc-300'}
                `}
              >
                <div className="flex items-center gap-2 overflow-hidden">
                  {opt.icon ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={opt.icon}
                      alt="icon"
                      className="w-6 h-6 rounded-full object-cover border border-zinc-700/50 bg-zinc-800 flex-shrink-0"
                    />
                  ) : opt.initials ? (
                    <div className="w-6 h-6 rounded-full bg-indigo-500/20 text-indigo-400 flex items-center justify-center text-[10px] font-bold border border-indigo-500/30 flex-shrink-0 overflow-hidden">
                      {opt.initials}
                    </div>
                  ) : null}
                  <div className="text-sm font-medium truncate">{opt.label}</div>
                </div>

                {opt.meta && <div className="text-[10px] text-zinc-600 font-mono ml-2 flex-shrink-0">{opt.meta}</div>}
              </div>
            ))}
            {filteredOptions.length === 0 && <div className="p-3 text-center text-zinc-500 text-xs">No results</div>}
          </Popover.Content>
        </Popover.Portal>
      </div>
    </Popover.Root>
  );
}
