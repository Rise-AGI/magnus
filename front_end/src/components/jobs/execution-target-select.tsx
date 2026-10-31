"use client";

import { EXECUTION_TARGETS } from "@/lib/config";
import { useLanguage } from "@/context/language-context";
import { SearchableSelect } from "@/components/ui/searchable-select";

export function ExecutionTargetSelect({
  value,
  onChange,
  disabled = false,
  className = "",
  servicesOnly = false,
}: {
  value: string;
  onChange: (id: string) => void;
  disabled?: boolean;
  className?: string;
  servicesOnly?: boolean;
}) {
  const { t } = useLanguage();
  const targets = EXECUTION_TARGETS.filter(target => !servicesOnly || target.services_supported);
  return (
    <SearchableSelect
      label={t("executionTarget.label")}
      value={value}
      options={targets.map(target => ({ value: target.id, label: target.name }))}
      onChange={onChange}
      disabled={disabled}
      clearable={false}
      className={className}
      contentClassName="max-h-[min(20rem,var(--radix-popover-content-available-height))]"
    />
  );
}
