// front_end/src/components/ui/dynamic-form/types.ts

export type FieldType = "text" | "number" | "float" | "boolean" | "select";

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