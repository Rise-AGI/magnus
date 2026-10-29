// front_end/src/app/page.tsx
import { redirect } from "next/navigation";
import { DEFAULT_ROUTE } from "@/lib/config";
import { executionTargetHref } from "@/lib/execution-target-navigation";

export default function Home({ searchParams }: { searchParams: { execution_target?: string | string[] } }) {
  const target = searchParams.execution_target;
  redirect(executionTargetHref(DEFAULT_ROUTE, typeof target === "string" ? target : null));
}