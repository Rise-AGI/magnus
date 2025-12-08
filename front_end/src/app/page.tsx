import { redirect } from "next/navigation";

export default function Home() {
  // 暂时让根路径直接跳到 Jobs 页面方便调试
  redirect("/jobs");
}