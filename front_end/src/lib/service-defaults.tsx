// front_end/src/lib/service-defaults.tsx
import React from "react";

// Single source of truth for service implicit export
export const SERVICE_IMPLICIT_EXPORT = `export MAGNUS_PORT=<available_port>`;

// Styled version for display
// showDollarSign: true for form (aligns with user input), false for detail page
export function ServiceImplicitExport({ showDollarSign = false }: { showDollarSign?: boolean }) {
  return (
    <>
      {showDollarSign && <span className="text-zinc-600">$ </span>}
      <span className="text-blue-400">export</span> MAGNUS_PORT=<span className="text-zinc-500">&lt;available_port&gt;</span>
    </>
  );
}
