// front_end/src/components/jobs/form-ui.tsx
"use client";

import { useState, useEffect, useRef, useMemo } from "react";
import { ChevronDown, ChevronUp, X, Search } from "lucide-react";

// --- 常量与类型 ---
// 修改点 1: 根据实际硬件，最大限制改为 2
export const MAX_GPU_COUNT = 2; 

// 修改点 2: 仅保留 5090 和新增 CPU 选项
export const GPU_TYPES = [
  { label: "NVIDIA GeForce RTX 5090", value: "RTX_5090", meta: "32GB • Blackwell" },
  { label: "CPU Only (No GPU)", value: "CPU", meta: "System RAM Only" },
];

interface SearchableSelectProps {
  label: string;
  value: string;
  options: { label: string; value: string; meta?: string }[];
  onChange: (val: string) => void;
  placeholder?: string;
  disabled?: boolean;
  hasError?: boolean;
  id?: string;
}

export function SearchableSelect({ label, value, options, onChange, placeholder, disabled, hasError, id }: SearchableSelectProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [query, setQuery] = useState("");
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const selectedOption = options.find(o => o.value === value);
    if (selectedOption) setQuery(selectedOption.label);
    else if (!value) setQuery("");
  }, [value, options]);

  // 点击外部关闭
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setIsOpen(false);
        const selectedOption = options.find(o => o.value === value);
        setQuery(selectedOption ? selectedOption.label : "");
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
    <div className="relative mb-4" ref={containerRef} id={id}>
      <label className={`text-xs uppercase tracking-wider mb-1.5 block font-medium transition-colors ${hasError ? 'text-red-500' : 'text-zinc-500'}`}>
        {label} {hasError && "*"}
      </label>
      <div className="relative group">
        <input
          ref={inputRef}
          type="text"
          disabled={disabled}
          className={`w-full bg-zinc-950 border px-3 py-2.5 pr-10 rounded-lg text-sm text-white outline-none transition-all placeholder-zinc-600 
            disabled:cursor-not-allowed disabled:text-zinc-500 disabled:bg-zinc-900/50
            ${hasError ? 'animate-shake border-red-500' : isOpen ? 'border-blue-500 ring-1 ring-blue-500/20' : 'border-zinc-800 hover:border-zinc-700'}
          `}
          placeholder={disabled ? "Waiting for scan..." : (placeholder || "Search...")}
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
      
      {/* Dropdown Options */}
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

// 修改点 3: 增加 disabled 属性支持，并允许数值为 0 (适配 CPU 模式)
export function GpuCountInput({ value, onChange, disabled }: { value: number, onChange: (v: number) => void, disabled?: boolean }) {
  // 如果 disabled (CPU模式)，不响应加减操作
  const handleIncrement = () => { if (!disabled && value < MAX_GPU_COUNT) onChange(value + 1); };
  const handleDecrement = () => { if (!disabled && value > 1) onChange(value - 1); };
  
  return (
    <div className={`mb-4 transition-opacity ${disabled ? 'opacity-50 cursor-not-allowed' : ''}`}>
      <label className="text-xs text-zinc-500 uppercase tracking-wider mb-1.5 block font-medium">GPU Count</label>
      <div className={`flex items-center bg-zinc-950 border rounded-lg overflow-hidden transition-colors
          ${disabled ? 'border-zinc-800 bg-zinc-900' : 'border-zinc-800 focus-within:border-blue-500 focus-within:ring-1 focus-within:ring-blue-500/20'}
      `}>
        <input 
          type="number" 
          disabled={disabled}
          min={0} max={MAX_GPU_COUNT} 
          value={value}
          onChange={(e) => {
            let val = parseInt(e.target.value);
            if (isNaN(val)) val = 0;
            // 允许 0，为了显示 "0" 当选择 CPU 时
            if (val > MAX_GPU_COUNT) val = MAX_GPU_COUNT;
            if (val < 0) val = 0;
            onChange(val);
          }}
          className={`w-full py-2.5 pl-4 text-white text-sm font-mono bg-transparent outline-none hide-arrows ${disabled ? 'text-zinc-500 cursor-not-allowed' : ''}`}
        />
        <div className={`flex flex-col border-l border-zinc-800 w-8 bg-zinc-900/30 ${disabled ? 'pointer-events-none' : ''}`}>
          <button onClick={handleIncrement} disabled={disabled || value >= MAX_GPU_COUNT} className="flex-1 hover:bg-zinc-800 text-zinc-400 hover:text-white flex items-center justify-center border-b border-zinc-800 disabled:opacity-20">
            <ChevronUp className="w-3 h-3" />
          </button>
          <button onClick={handleDecrement} disabled={disabled || value <= 1} className="flex-1 hover:bg-zinc-800 text-zinc-400 hover:text-white flex items-center justify-center disabled:opacity-20">
            <ChevronDown className="w-3 h-3" />
          </button>
        </div>
      </div>
    </div>
  );
}