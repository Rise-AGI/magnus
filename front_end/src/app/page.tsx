// front_end/src/app/page.tsx
import { redirect } from "next/navigation";

export default function Home() {
  redirect("/explorer");
}