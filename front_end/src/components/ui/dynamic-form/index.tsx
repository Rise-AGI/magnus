// front_end/src/components/ui/dynamic-form/index.tsx
import React, { useState, useRef, useEffect } from "react";
import { Loader2, ChevronDown, ChevronRight } from "lucide-react";
import { NumberStepper } from "@/components/ui/number-stepper";
import { SearchableSelect } from "@/components/ui/searchable-select";
import { cn } from "@/lib/utils";
import { FieldSchema } from "./types";

interface DynamicFormProps {
  schema: FieldSchema[];
  values: Record<string, any>;
  onChange: (key: string, value: any) => void;
  isLoading?: boolean;
  emptyMessage?: string;
  errorField?: string | null;
}

function DynamicStringInput({ 
  field, 
  value, 
  onChange,
  hasError 
}: { 
  field: FieldSchema; 
  value: string; 
  onChange: (val: string) => void;
  hasError?: boolean;
}) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [isFocused, setIsFocused] = useState(false);

  useEffect(() => {
    if (field.multi_line && textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = `${textareaRef.current.scrollHeight}px`;
    }
  }, [value, field.multi_line]);

  const baseClasses = cn(
    "w-full bg-zinc-950 border px-3 py-2.5 rounded-lg text-sm transition-all outline-none placeholder-zinc-700",
    field.multi_line && "font-mono leading-relaxed resize-none overflow-hidden", 
    (field.multi_line && !field.min_lines) && "min-h-[42px]", 
    hasError 
      ? "border-red-500 animate-shake" 
      : (!field.border_color 
          ? "border-zinc-800 focus:border-blue-500 focus:ring-1 focus:ring-blue-500/20" 
          : "border-zinc-800")
  );

  const dynamicStyle: React.CSSProperties = {
    color: field.color,
    ...(field.border_color && !hasError && isFocused && {
      borderColor: field.border_color,
      boxShadow: `0 0 0 1px ${field.border_color}, 0 0 15px color-mix(in srgb, ${field.border_color}, transparent 80%)`
    })
  };

  const commonProps = {
    value,
    onChange: (e: React.ChangeEvent<HTMLTextAreaElement | HTMLInputElement>) => onChange(e.target.value),
    onFocus: () => setIsFocused(true),
    onBlur: () => setIsFocused(false),
    placeholder: field.placeholder,
    className: baseClasses,
    style: dynamicStyle,
    spellCheck: false,
  };

  if (field.multi_line) {
    return <textarea ref={textareaRef} rows={field.min_lines || 1} {...commonProps} />;
  }

  return <input type="text" {...commonProps} />;
}

function FormField({ 
  field, 
  value, 
  onChange,
  isError 
}: { 
  field: FieldSchema; 
  value: any; 
  onChange: (key: string, val: any) => void;
  isError?: boolean;
}) {
  const showRequiredStar = field.type === "text" && field.allow_empty === false;

  return (
    <div className="space-y-1.5" id={`field-${field.key}`}>
      <label className={cn(
        "text-xs uppercase tracking-wider mb-1.5 block font-medium transition-colors",
        isError ? "text-red-500" : "text-zinc-500"
      )}>
        {field.label || field.key}
        {showRequiredStar && <span className="text-red-500 ml-0.5">*</span>}
      </label>

      {field.type === "number" ? (
        <NumberStepper
          label=""
          value={Number(value)}
          onChange={(val) => onChange(field.key, val)}
          min={field.min}
          max={field.max}
        />
      ) : field.type === "select" ? (
        <SearchableSelect
          value={String(value)}
          onChange={(val) => onChange(field.key, val)}
          options={(field.options || []).map(opt => ({
            label: opt.label,
            value: String(opt.value),
            meta: opt.description 
          }))}
          placeholder={field.placeholder || "Select option..."}
          className="mb-0"
        />
      ) : field.type === "boolean" ? (
        <SearchableSelect
          value={String(value)}
          onChange={(val) => onChange(field.key, val === "true")}
          options={[
            { label: "True", value: "true" },
            { label: "False", value: "false" }
          ]}
          placeholder="Select boolean..."
          className="mb-0"
        />
      ) : (
        <DynamicStringInput 
          field={field} 
          value={value} 
          onChange={(val) => onChange(field.key, val)} 
          hasError={isError}
        />
      )}

      {field.description && (
        <p className="text-[11px] text-zinc-500 mt-1 ml-0.5">
          {field.description}
        </p>
      )}
    </div>
  );
}

export function DynamicForm({
  schema,
  values,
  onChange,
  isLoading = false,
  emptyMessage = "No parameters required.",
  errorField = null
}: DynamicFormProps) {
  const [expandedScopes, setExpandedScopes] = useState<Record<string, boolean>>(() => {
    const defaults: Record<string, boolean> = {};
    schema.forEach(f => {
      // Auto-expand scope if it contains mandatory fields
      if (f.scope && f.allow_empty === false) {
        defaults[f.scope] = true;
      }
    });
    return defaults;
  });

  useEffect(() => {
    if (errorField) {
      const field = schema.find(f => f.key === errorField);
      if (field && field.scope) {
        setExpandedScopes(prev => ({ ...prev, [field.scope!]: true }));
      }
    }
  }, [errorField, schema]);

  const toggleScope = (scopeName: string) => {
    setExpandedScopes(prev => ({ ...prev, [scopeName]: !prev[scopeName] }));
  };

  if (isLoading) {
    return (
      <div className="py-10 flex justify-center">
        <Loader2 className="w-6 h-6 animate-spin text-zinc-600" />
      </div>
    );
  }

  if (schema.length === 0) {
    return <div className="text-center text-zinc-500 py-4">{emptyMessage}</div>;
  }

  const mainFields = schema.filter(f => !f.scope);
  const scopedFieldsMap: Record<string, FieldSchema[]> = {};
  
  schema.forEach(f => {
    if (f.scope) {
      if (!scopedFieldsMap[f.scope]) scopedFieldsMap[f.scope] = [];
      scopedFieldsMap[f.scope].push(f);
    }
  });
  
  const sortedScopeNames = Object.keys(scopedFieldsMap).sort();

  return (
    <div className="space-y-6">
      {mainFields.length > 0 && (
        <div className="space-y-5">
          {mainFields.map(field => (
            <FormField 
              key={field.key} 
              field={field} 
              value={values[field.key]} 
              onChange={onChange}
              isError={errorField === field.key}
            />
          ))}
        </div>
      )}

      {sortedScopeNames.map(scopeName => {
        const isExpanded = !!expandedScopes[scopeName];
        const fields = scopedFieldsMap[scopeName];

        return (
          <div key={scopeName} className="pt-2 border-t border-zinc-800/50">
            <button 
              type="button" 
              onClick={() => toggleScope(scopeName)}
              className="flex items-center gap-2 text-sm font-medium text-zinc-400 hover:text-zinc-200 transition-colors select-none group w-full text-left py-2"
            >
              <div className="text-zinc-600 group-hover:text-zinc-300 transition-colors">
                {isExpanded ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
              </div>
              <span>{scopeName}</span>
            </button>
            
            {isExpanded && (
              <div className="mt-3 grid grid-cols-1 gap-5 animate-in slide-in-from-top-1 duration-200">
                {fields.map(field => (
                   <FormField 
                     key={field.key} 
                     field={field} 
                     value={values[field.key]} 
                     onChange={onChange}
                     isError={errorField === field.key}
                   />
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}