// front_end/src/components/ui/dynamic-form/index.tsx
import React, { useState, useRef, useEffect } from "react";
import { Loader2, ChevronDown, ChevronRight, Plus, X } from "lucide-react";
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
  hasError,
  disabled,
  isVisible,
}: {
  field: FieldSchema;
  value: string;
  onChange: (val: string) => void;
  hasError?: boolean;
  disabled?: boolean;
  isVisible?: boolean;
}) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [isFocused, setIsFocused] = useState(false);

  useEffect(() => {
    if (field.multi_line && textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = `${Math.max(textareaRef.current.scrollHeight, field.min_lines ? field.min_lines * 24 : 42)}px`;
    }
  }, [value, field.multi_line, field.min_lines, isVisible]);

  const baseClasses = cn(
    "w-full bg-zinc-950 border px-3 py-2.5 rounded-lg text-sm transition-all outline-none placeholder-zinc-700",
    field.multi_line && "font-mono leading-relaxed resize-none overflow-hidden",
    (field.multi_line && !field.min_lines) && "min-h-[42px]",
    disabled && "opacity-40 cursor-not-allowed",
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
    disabled,
  };

  if (field.multi_line) {
    return <textarea ref={textareaRef} rows={field.min_lines || 1} {...commonProps} />;
  }

  return <input type="text" {...commonProps} />;
}


function DynamicFloatInput({
  field,
  value,
  onChange,
  hasError,
  disabled,
}: {
  field: FieldSchema;
  value: string;
  onChange: (val: string) => void;
  hasError?: boolean;
  disabled?: boolean;
}) {
  const baseClasses = cn(
    "w-full bg-zinc-950 border px-3 py-2.5 rounded-lg text-sm font-mono transition-all outline-none placeholder-zinc-700",
    disabled && "opacity-40 cursor-not-allowed",
    hasError
      ? "border-red-500 animate-shake"
      : "border-zinc-800 focus:border-blue-500 focus:ring-1 focus:ring-blue-500/20"
  );

  return (
    <input
      type="text"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={field.placeholder || "e.g. 3.14, 1e-5"}
      className={baseClasses}
      spellCheck={false}
      disabled={disabled}
    />
  );
}


function SingleFieldInput({
  field,
  value,
  onChange,
  hasError,
  disabled,
  isVisible,
}: {
  field: FieldSchema;
  value: any;
  onChange: (val: any) => void;
  hasError?: boolean;
  disabled?: boolean;
  isVisible?: boolean;
}) {
  if (field.type === "number") {
    return (
      <div className={cn(disabled && "opacity-40 pointer-events-none")}>
        <NumberStepper
          label=""
          value={Number(value)}
          onChange={onChange}
          min={field.min}
          max={field.max}
        />
      </div>
    );
  }

  if (field.type === "float") {
    return (
      <DynamicFloatInput
        field={field}
        value={String(value ?? "")}
        onChange={onChange}
        hasError={hasError}
        disabled={disabled}
      />
    );
  }

  if (field.type === "select") {
    return (
      <div className={cn(disabled && "opacity-40 pointer-events-none")}>
        <SearchableSelect
          value={String(value)}
          onChange={onChange}
          options={(field.options || []).map(opt => ({
            label: opt.label,
            value: String(opt.value),
            meta: opt.description
          }))}
          placeholder={field.placeholder || "Select option..."}
          className="mb-0"
        />
      </div>
    );
  }

  if (field.type === "boolean") {
    return (
      <div className={cn(disabled && "opacity-40 pointer-events-none")}>
        <SearchableSelect
          value={String(value)}
          onChange={(val) => onChange(val === "true")}
          options={[
            { label: "True", value: "true" },
            { label: "False", value: "false" }
          ]}
          placeholder="Select boolean..."
          className="mb-0"
        />
      </div>
    );
  }

  return (
    <DynamicStringInput
      field={field}
      value={value ?? ""}
      onChange={onChange}
      hasError={hasError}
      disabled={disabled}
      isVisible={isVisible}
    />
  );
}


function ListField({
  field,
  values,
  onChange,
  isError,
  disabled,
  isVisible,
}: {
  field: FieldSchema;
  values: any[];
  onChange: (val: any[]) => void;
  isError?: boolean;
  disabled?: boolean;
  isVisible?: boolean;
}) {
  const items = Array.isArray(values) ? values : [];

  const getDefaultValue = () => {
    if (field.type === "number") return 0;
    if (field.type === "boolean") return false;
    if (field.type === "select" && field.options?.length) return field.options[0].value;
    return "";
  };

  const handleAdd = () => {
    onChange([...items, getDefaultValue()]);
  };

  const handleRemove = (index: number) => {
    onChange(items.filter((_, i) => i !== index));
  };

  const handleItemChange = (index: number, value: any) => {
    const newItems = [...items];
    newItems[index] = value;
    onChange(newItems);
  };

  return (
    <div className={cn("space-y-2", disabled && "opacity-40")}>
      {items.map((item, index) => (
        <div key={index} className="flex items-start gap-2">
          <div className="flex-1">
            <SingleFieldInput
              field={field}
              value={item}
              onChange={(val) => handleItemChange(index, val)}
              hasError={isError}
              disabled={disabled}
              isVisible={isVisible}
            />
          </div>
          {!disabled && (
            <button
              type="button"
              onClick={() => handleRemove(index)}
              className="mt-2.5 p-1 text-zinc-600 hover:text-red-400 transition-colors"
            >
              <X className="w-4 h-4" />
            </button>
          )}
        </div>
      ))}

      {!disabled && (
        <button
          type="button"
          onClick={handleAdd}
          className="flex items-center gap-1.5 text-xs text-zinc-500 hover:text-zinc-300 transition-colors py-1.5"
        >
          <Plus className="w-3.5 h-3.5" />
          <span>Add item</span>
        </button>
      )}
    </div>
  );
}


function FormField({
  field,
  value,
  onChange,
  isError,
  isVisible,
}: {
  field: FieldSchema;
  value: any;
  onChange: (key: string, val: any) => void;
  isError?: boolean;
  isVisible?: boolean;
}) {
  const showRequiredStar = field.type === "text" && field.allow_empty === false;

  // Optional 字段：value 为 null 表示禁用
  const isOptionalEnabled = field.is_optional ? value !== null : true;

  const handleToggleOptional = () => {
    if (field.is_optional) {
      if (isOptionalEnabled) {
        onChange(field.key, null);
      } else {
        // 启用时给一个默认值
        let defaultVal: any = field.default ?? "";
        if (field.is_list) defaultVal = [];
        else if (field.type === "number") defaultVal = field.default ?? 0;
        else if (field.type === "boolean") defaultVal = field.default ?? false;
        else if (field.type === "select" && field.options?.length) defaultVal = field.default ?? field.options[0].value;
        onChange(field.key, defaultVal);
      }
    }
  };

  const handleValueChange = (val: any) => {
    onChange(field.key, val);
  };

  // 标签部分
  const labelContent = (
    <div className="flex items-center justify-between mb-1.5">
      <label className={cn(
        "text-xs uppercase tracking-wider font-medium transition-colors",
        isError ? "text-red-500" : (isOptionalEnabled ? "text-zinc-500" : "text-zinc-600")
      )}>
        {field.label || field.key}
        {showRequiredStar && <span className="text-red-500 ml-0.5">*</span>}
        {field.is_list && (
          <span className="text-zinc-600 ml-1.5 normal-case tracking-normal font-normal">(list)</span>
        )}
      </label>

      {field.is_optional && (
        <button
          type="button"
          onClick={handleToggleOptional}
          className={cn(
            "relative w-8 h-4 rounded-full transition-colors",
            isOptionalEnabled ? "bg-blue-600" : "bg-zinc-700"
          )}
        >
          <span className={cn(
            "absolute top-0.5 w-3 h-3 rounded-full bg-white transition-transform",
            isOptionalEnabled ? "left-[18px]" : "left-0.5"
          )} />
        </button>
      )}
    </div>
  );

  // 渲染字段内容
  const renderContent = () => {
    if (field.is_list) {
      return (
        <ListField
          field={field}
          values={isOptionalEnabled ? (value ?? []) : []}
          onChange={handleValueChange}
          isError={isError}
          disabled={!isOptionalEnabled}
          isVisible={isVisible}
        />
      );
    }

    return (
      <SingleFieldInput
        field={field}
        value={isOptionalEnabled ? value : ""}
        onChange={handleValueChange}
        hasError={isError}
        disabled={!isOptionalEnabled}
        isVisible={isVisible}
      />
    );
  };

  return (
    <div className="space-y-1.5" id={`field-${field.key}`}>
      {labelContent}
      {renderContent()}
      {field.description && (
        <p className={cn(
          "text-[11px] mt-1 ml-0.5 transition-colors",
          isOptionalEnabled ? "text-zinc-500" : "text-zinc-600"
        )}>
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
              isVisible={true}
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
                     isVisible={isExpanded}
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