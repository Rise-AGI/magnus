// front_end/src/components/layout/sidebar.tsx
"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { LayoutDashboard, Rocket, Activity, Server } from "lucide-react";


const NAV_ITEMS = [
  { name: "Dashboard", href: "/dashboard", icon: LayoutDashboard },
  { name: "Jobs", href: "/jobs", icon: Rocket },
  { name: "Cluster", href: "/cluster", icon: Activity },
];


export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="w-64 h-screen fixed left-0 top-0 border-r border-zinc-800 bg-zinc-950/50 backdrop-blur-xl flex flex-col z-50">
      {/* Logo Area */}
      <div className="h-16 flex items-center px-6 border-b border-zinc-800">
        <div className="font-bold text-xl tracking-tighter">
          Magnus<span className="text-blue-500">Platform</span>
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex-1 py-6 px-3 space-y-1">
        {NAV_ITEMS.map((item) => {
          const isActive = pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`flex items-center gap-3 px-3 py-2.5 rounded-lg transition-all text-sm font-medium
                ${isActive 
                  ? "bg-blue-600/10 text-blue-400" 
                  : "text-zinc-400 hover:bg-zinc-900 hover:text-zinc-100"
                }`}
            >
              <item.icon className="w-4 h-4" />
              {item.name}
            </Link>
          );
        })}
      </nav>

      {/* Footer Info */}
      <div className="p-4 border-t border-zinc-800 text-xs text-zinc-600">
        <div className="flex items-center gap-2 mb-1">
          <Server className="w-3 h-3" />
          <span>v0.1.0-alpha</span>
        </div>
        PKU-Plasma
      </div>
    </aside>
  );
}