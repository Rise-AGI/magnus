// front_end/src/components/ui/dynamic-form/types.ts

export type FieldType = "text" | "number" | "float" | "boolean" | "select" | "file_secret";

export interface FieldOption {
  label: string;
  value: string | number;
  description?: string;
}

export interface FieldSchema {
  key: string;
  label: string;
  type: FieldType;
  default?: any;
  description?: string;
  scope?: string | null;
  is_optional?: boolean;
  is_list?: boolean;

  // Int specific
  min?: number;
  max?: number;

  // Str specific
  placeholder?: string;
  multi_line?: boolean;
  min_lines?: number;
  color?: string;
  border_color?: string;
  allow_empty?: boolean;

  // Select/Literal specific
  options?: FieldOption[];
}


/**
 * 计算字段的初始值。
 * Optional 字段：default 为 null/undefined 时返回 null（禁用），否则返回 default（启用）
 * 非 Optional 字段：返回 default 或类型对应的空值
 */
export function getFieldInitialValue(field: FieldSchema, cached?: any): any {
  if (cached !== undefined) return cached;

  if (field.is_optional) {
    return field.default ?? null;
  }

  if (field.default !== undefined && field.default !== null) {
    return field.default;
  }

  if (field.is_list) return [];
  if (field.type === "number") return 0;
  if (field.type === "boolean") return false;
  return "";
}


/**
 * 校验单个字段值，返回错误信息或 null。
 * Optional 字段值为 null 时跳过校验（禁用状态）。
 * Optional 字段启用时，必须有有效值。
 */
export function validateFieldValue(field: FieldSchema, value: any): string | null {
  const label = field.label || field.key;

  // Optional 禁用状态，跳过
  if (field.is_optional && value === null) {
    return null;
  }

  // Optional 启用状态，数值类型必须有有效值
  if (field.is_optional) {
    const isEmpty = value === "" || value === undefined;
    if (isEmpty) {
      if (field.type === "float") return `${label} must be a valid number`;
      if (field.type === "number") return `${label} must be a valid integer`;
      // text/select/boolean/list 允许空值（除非有 allow_empty=false，后续校验会处理）
    }
  }

  // List 类型：逐项校验
  if (field.is_list && Array.isArray(value)) {
    for (let i = 0; i < value.length; i++) {
      const itemErr = validateSingleValue(field, value[i], `${label}[${i}]`);
      if (itemErr) return itemErr;
    }
    return null;
  }

  return validateSingleValue(field, value, label);
}


function validateSingleValue(field: FieldSchema, value: any, label: string): string | null {
  // 必填文本
  if (field.type === "text" && field.allow_empty === false) {
    if (!value || (typeof value === "string" && !value.trim())) {
      return `${label} is required`;
    }
  }

  // Float 校验
  if (field.type === "float") {
    const str = String(value ?? "").trim();
    if (str === "") return `${label} must be a valid number`;

    const num = Number(str);
    if (isNaN(num)) return `${label} must be a valid number`;
    if (field.min != null && num < field.min) return `${label} must be ≥ ${field.min}`;
    if (field.max != null && num > field.max) return `${label} must be ≤ ${field.max}`;
  }

  // Number (int) 校验
  if (field.type === "number") {
    const num = Number(value);
    if (isNaN(num)) return `${label} must be a valid integer`;
    if (field.min != null && num < field.min) return `${label} must be ≥ ${field.min}`;
    if (field.max != null && num > field.max) return `${label} must be ≤ ${field.max}`;
  }

  return null;
}