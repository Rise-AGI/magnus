// front_end/src/components/ui/searchable-select.tsx
"use client";

import { useState, useEffect, useRef, useMemo } from "react";
import { X, Search } from "lucide-react";

interface SearchableSelectProps {
  label?: string; // Label 变为可选，方便在 Filter Bar 等狭窄地方使用
  value: string;
  options: { label: string; value: string; meta?: string }[];
  onChange: (val: string) => void;
  placeholder?: string;
  disabled?: boolean;
  hasError?: boolean;
  id?: string;
  className?: string; // 允许从外部传入额外的样式
}

export function SearchableSelect({ 
  label, value, options, onChange, placeholder, disabled, hasError, id, className = "" 
}: SearchableSelectProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [query, setQuery] = useState("");
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const selectedOption = options.find(o => o.value === value);
    if (selectedOption) {
        setQuery(selectedOption.label);
    } else if (value) {
        setQuery(value);
    } else {
        setQuery("");
    }
  }, [value, options]);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setIsOpen(false);
        const selectedOption = options.find(o => o.value === value);
        if (selectedOption) setQuery(selectedOption.label);
        else if (value) setQuery(value);
        else setQuery("");
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [value, options]);

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

  return (
    <div className={`relative ${className}`} ref={containerRef} id={id}>
      {label && (
        <label className={`text-xs uppercase tracking-wider mb-1.5 block font-medium transition-colors ${hasError ? 'text-red-500' : 'text-zinc-500'}`}>
          {label} {hasError && "*"}
        </label>
      )}
      <div className="relative group">
        <input
          ref={inputRef}
          type="text"
          disabled={disabled}
          className={`w-full bg-zinc-950 border px-3 py-2.5 pr-10 rounded-lg text-sm text-white outline-none transition-all placeholder-zinc-600 
            disabled:cursor-not-allowed disabled:text-zinc-500 disabled:bg-zinc-900/50
            ${hasError ? 'animate-shake border-red-500' : isOpen ? 'border-blue-500 ring-1 ring-blue-500/20' : 'border-zinc-800 hover:border-zinc-700'}
          `}
          placeholder={disabled ? "Waiting..." : (placeholder || "Search...")}
          value={query}
          onChange={(e) => { setQuery(e.target.value); setIsOpen(true); }}
          onFocus={() => !disabled && setIsOpen(true)}
        />
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
      </div>
      
      {isOpen && !disabled && (
        <div className="absolute z-50 w-full mt-1 bg-[#0F0F11] border border-zinc-800 rounded-lg shadow-xl overflow-hidden max-h-60 overflow-y-auto custom-scrollbar">
          {filteredOptions.map((opt) => (
            <div 
              key={opt.value} 
              onClick={() => { onChange(opt.value); setQuery(opt.label); setIsOpen(false); }} 
              className={`px-3 py-2.5 cursor-pointer border-b border-zinc-800/50 last:border-0 hover:bg-blue-500/10 transition-colors
                ${opt.value === value ? 'bg-blue-500/20 border-l-2 border-l-blue-500' : 'border-l-2 border-l-transparent'}
              `}
            >
              <div className="text-sm text-zinc-200">{opt.label}</div>
              {opt.meta && <div className="text-xs text-zinc-500 mt-0.5 font-mono">{opt.meta}</div>}
            </div>
          ))}
          {filteredOptions.length === 0 && <div className="p-3 text-center text-zinc-500 text-xs">No results found</div>}
        </div>
      )}
    </div>
  );
}