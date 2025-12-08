// front_end/src/components/ui/number-stepper.tsx
"use client";

import { ChevronDown, ChevronUp } from "lucide-react";

interface NumberStepperProps {
  label?: string;
  value: number;
  onChange: (v: number) => void;
  min?: number;
  max?: number;
  disabled?: boolean;
}

export function NumberStepper({ 
  label, value, onChange, min = 0, max = 100, disabled = false 
}: NumberStepperProps) {
  
  const handleIncrement = () => { if (!disabled && value < max) onChange(value + 1); };
  const handleDecrement = () => { if (!disabled && value > min) onChange(value - 1); };
  
  return (
    <div className={`transition-opacity ${disabled ? 'opacity-50 cursor-not-allowed' : ''}`}>
      {label && (
        <label className="text-xs text-zinc-500 uppercase tracking-wider mb-1.5 block font-medium">
          {label}
        </label>
      )}
      <div className={`flex items-center bg-zinc-950 border rounded-lg overflow-hidden transition-colors
          ${disabled ? 'border-zinc-800 bg-zinc-900' : 'border-zinc-800 focus-within:border-blue-500 focus-within:ring-1 focus-within:ring-blue-500/20'}
      `}>
        <input 
          type="number" 
          disabled={disabled}
          min={min} max={max} 
          value={value}
          onChange={(e) => {
            let val = parseInt(e.target.value);
            if (isNaN(val)) val = min;
            if (val > max) val = max;
            if (val < min) val = min;
            onChange(val);
          }}
          className={`w-full py-2.5 pl-4 text-white text-sm font-mono bg-transparent outline-none hide-arrows ${disabled ? 'text-zinc-500 cursor-not-allowed' : ''}`}
        />
        <div className={`flex flex-col border-l border-zinc-800 w-8 bg-zinc-900/30 ${disabled ? 'pointer-events-none' : ''}`}>
          <button onClick={handleIncrement} disabled={disabled || value >= max} className="flex-1 hover:bg-zinc-800 text-zinc-400 hover:text-white flex items-center justify-center border-b border-zinc-800 disabled:opacity-20">
            <ChevronUp className="w-3 h-3" />
          </button>
          <button onClick={handleDecrement} disabled={disabled || value <= min} className="flex-1 hover:bg-zinc-800 text-zinc-400 hover:text-white flex items-center justify-center disabled:opacity-20">
            <ChevronDown className="w-3 h-3" />
          </button>
        </div>
      </div>
    </div>
  );
}