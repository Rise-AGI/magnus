// front_end/src/app/page.tsx
import { redirect } from "next/navigation";
import { DEFAULT_ROUTE } from "@/lib/config";

export default function Home() {
  redirect(DEFAULT_ROUTE);
}