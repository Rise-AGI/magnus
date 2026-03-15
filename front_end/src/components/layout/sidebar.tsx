// front_end/src/components/layout/sidebar.tsx
"use client";

import Link from "next/link";
import Image from "next/image";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  Rocket,
  Server,
  LogIn,
  LogOut,
  ScrollText,
  Layers,
  ArrowRight,
  Dna,
  Construction,
  Users,
  Waypoints,
  Container,
  Compass,
} from "lucide-react";
import { useAuth } from "@/context/auth-context";
import { useLanguage } from "@/context/language-context";
import { CLUSTER_CONFIG, IS_LOCAL_MODE } from "@/lib/config";

interface NavItem {
  i18nKey: string;
  href: string;
  icon: typeof ArrowRight;
  wip?: boolean;
}

const NAV_ITEMS: NavItem[] = [
  { i18nKey: "nav.milestones", href: "/milestones", icon: Compass, wip: true },
  { i18nKey: "nav.explorer", href: "/explorer", icon: ArrowRight },
  { i18nKey: "nav.people", href: "/people", icon: Users },
  { i18nKey: "nav.motions", href: "/motions", icon: Waypoints, wip: true },
  { i18nKey: "nav.jobs", href: "/jobs", icon: Rocket },
  { i18nKey: "nav.blueprints", href: "/blueprints", icon: ScrollText },
  { i18nKey: "nav.services", href: "/services", icon: Layers },
  { i18nKey: "nav.skills", href: "/skills", icon: Dna },
  { i18nKey: "nav.images", href: "/images", icon: Container },
  { i18nKey: "nav.cluster", href: "/cluster", icon: LayoutDashboard },
];

export function Sidebar() {
  const pathname = usePathname();
  const { user, login, logout, isLoading } = useAuth();
  const { t } = useLanguage(); 

  return (
    <aside className="w-full h-full bg-zinc-950/50 backdrop-blur-xl flex flex-col">
      
      {/* Logo Header */}
      <div className="h-16 flex items-center justify-between px-8 border-b border-zinc-800 bg-zinc-900/20">
        <div className="font-bold text-xl tracking-tighter text-zinc-100 cursor-default select-none">
          Magnus<span className="text-blue-500">Platform</span>
        </div>
        <Image
          src="/api/logo"
          alt="Magnus Logo"
          width={28}
          height={28}
          className="rounded-full object-cover border border-zinc-700/50 shadow-sm opacity-90 hover:opacity-100 transition-opacity"
          unoptimized
        />
      </div>

      {/* Navigation Links */}
      <nav className="flex-1 py-6 px-3">
        <div className="space-y-1">
          {NAV_ITEMS.map((item) => {
            const isActive = pathname === item.href || pathname.startsWith(item.href + '/');
            const label = t(item.i18nKey as any);

            return (
              <Link
                key={item.href}
                href={item.href}
                className={`flex items-center gap-3 px-3 py-2.5 rounded-lg transition-all text-sm font-medium group ${
                  isActive
                    ? "bg-blue-600/10 text-blue-400 border border-blue-600/10 shadow-[0_0_15px_rgba(37,99,235,0.1)]"
                    : "text-zinc-400 hover:bg-zinc-900 hover:text-zinc-100 border border-transparent"
                }`}
              >
                <item.icon className={`w-4 h-4 transition-colors ${
                  isActive ? "text-blue-400" : "text-zinc-500 group-hover:text-zinc-300"
                }`} />
                {label}
                {item.wip && (
                  <Construction className="w-3 h-3 text-zinc-600 ml-auto" />
                )}
              </Link>
            );
          })}
        </div>
      </nav>

      {/* Footer / User Profile */}
      <div className="border-t border-zinc-800 bg-zinc-900/20 p-3 flex flex-col gap-3">
        <div>
          {isLoading ? (
            <div className="h-12 animate-pulse bg-zinc-900 rounded-lg border border-zinc-800"></div>
          ) : !user ? (
            <button
              onClick={login}
              className="w-full flex items-center justify-center gap-2 px-3 py-2.5 rounded-lg transition-all text-sm font-medium bg-zinc-900 hover:bg-zinc-800 text-zinc-300 hover:text-white border border-zinc-800 hover:border-zinc-700"
            >
              <LogIn className="w-4 h-4" />
              <span>{IS_LOCAL_MODE ? t("auth.signIn") : t("auth.signInWithFeishu")}</span>
            </button>
          ) : (
            <div className="flex items-center gap-3 px-3 py-2.5 rounded-xl bg-zinc-900 border border-zinc-800 shadow-sm">
              <div className="w-9 h-9 rounded-full bg-zinc-800 border border-zinc-700/50 flex-shrink-0 overflow-hidden flex items-center justify-center">
                 {user.avatar_url ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={user.avatar_url} alt={user.name} className="w-full h-full object-cover" />
                 ) : (
                    <span className="text-xs font-bold text-zinc-400">{user.name.substring(0, 1).toUpperCase()}</span>
                 )}
              </div>
              
              <div className="flex-1 min-w-0 flex flex-col justify-center">
                <p className="text-sm font-semibold text-zinc-200 truncate leading-none mb-1">{user.name}</p>
                <p className="text-[10px] text-zinc-500 truncate font-mono" title={user.email || ""}>
                    {user.email || ""}
                </p>
              </div>

              <button
                onClick={logout}
                className="p-1.5 rounded-md text-zinc-500 hover:text-red-400 hover:bg-red-400/10 transition-colors flex-shrink-0"
                title={t("auth.logout")}
              >
                <LogOut className="w-4 h-4" />
              </button>
            </div>
          )}
        </div>

        <div className="px-2 pb-1 text-[10px] tracking-wider text-zinc-600 flex justify-between items-center font-medium">
          <span>{CLUSTER_CONFIG.name}</span>
          <div className="flex items-center gap-1.5">
            <Server className="w-3 h-3" />
            <span>v0.1.0</span>
          </div>
        </div>
      </div>
    </aside>
  );
}