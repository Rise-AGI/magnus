import { Sidebar } from "@/components/layout/sidebar";
import { Header } from "@/components/layout/header";

export default function MainLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-[#050505]">
      <Sidebar />
      <div className="pl-64">
        <Header />
        <main className="p-8">
          {children}
        </main>
      </div>
    </div>
  );
}