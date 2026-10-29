export const EXECUTION_TARGET_STORAGE_KEY = "magnus_execution_target";

export function executionTargetHref(href: string, target?: string | null): string {
  if (!target) return href;
  const url = new URL(href, "http://magnus.invalid");
  url.searchParams.set("execution_target", target);
  return url.pathname + url.search + url.hash;
}
