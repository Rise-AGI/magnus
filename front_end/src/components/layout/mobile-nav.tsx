// front_end/src/components/layout/mobile-nav.tsx
"use client";

import { useState, useEffect, useCallback } from "react";
import Link from "next/link";
import Image from "next/image";
import { usePathname } from "next/navigation";
import {
  Menu,
  X,
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
import { AvatarCircle } from "@/components/ui/user-avatar";
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

export function MobileNav() {
  const [open, setOpen] = useState(false);
  const pathname = usePathname();
  const { user, login, logout, isLoading } = useAuth();
  const { t } = useLanguage();

  // Close on route change
  useEffect(() => {
    setOpen(false);
  }, [pathname]);

  // Prevent body scroll when open
  useEffect(() => {
    if (open) {
      document.body.style.overflow = "hidden";
    } else {
      document.body.style.overflow = "";
    }
    return () => { document.body.style.overflow = ""; };
  }, [open]);

  const toggle = useCallback(() => setOpen(prev => !prev), []);

  return (
    <>
      {/* Hamburger button - only visible below md */}
      <button
        onClick={toggle}
        className="md:hidden p-3 text-zinc-400 hover:text-zinc-200 active:scale-95 transition-all rounded-lg"
        aria-label="Toggle navigation"
      >
        <Menu className="w-5 h-5" />
      </button>

      {/* Overlay + Drawer */}
      {open && (
        <div className="fixed inset-0 z-50 md:hidden">
          {/* Backdrop */}
          <div
            className="absolute inset-0 bg-black/60 backdrop-blur-sm"
            onClick={() => setOpen(false)}
          />

          {/* Drawer */}
          <aside className="absolute inset-y-0 left-0 w-72 max-w-[85vw] bg-zinc-950 border-r border-zinc-800 flex flex-col animate-in slide-in-from-left duration-200">
            {/* Header */}
            <div className="h-16 flex items-center justify-between px-6 border-b border-zinc-800 bg-zinc-900/20">
              <div className="font-bold text-xl tracking-tighter text-zinc-100 cursor-default select-none">
                Magnus<span className="text-blue-500">Platform</span>
              </div>
              <button
                onClick={() => setOpen(false)}
                className="p-2.5 text-zinc-500 hover:text-zinc-300 active:scale-95 transition-all rounded-lg"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            {/* Nav links */}
            <nav className="flex-1 py-4 px-3 overflow-y-auto">
              <div className="space-y-1">
                {NAV_ITEMS.map((item) => {
                  const isActive = pathname === item.href || pathname.startsWith(item.href + "/");
                  const label = t(item.i18nKey as any);

                  return (
                    <Link
                      key={item.href}
                      href={item.href}
                      className={`flex items-center gap-3 px-3 py-3 rounded-lg transition-all text-sm font-medium active:scale-[0.98] ${
                        isActive
                          ? "bg-blue-600/10 text-blue-400 border border-blue-600/10"
                          : "text-zinc-400 hover:bg-zinc-900 hover:text-zinc-100 border border-transparent active:bg-zinc-800"
                      }`}
                    >
                      <item.icon className={`w-4 h-4 transition-colors ${
                        isActive ? "text-blue-400" : "text-zinc-500"
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

            {/* Footer */}
            <div className="flex-shrink-0 border-t border-zinc-800 bg-zinc-900/20 p-3 flex flex-col gap-3">
              {isLoading ? (
                <div className="h-12 animate-pulse bg-zinc-900 rounded-lg border border-zinc-800" />
              ) : !user ? (
                <button
                  onClick={login}
                  className="w-full flex items-center justify-center gap-2 px-3 py-2.5 rounded-lg text-sm font-medium bg-zinc-900 hover:bg-zinc-800 text-zinc-300 border border-zinc-800 active:scale-95 transition-all"
                >
                  <LogIn className="w-4 h-4" />
                  <span>{IS_LOCAL_MODE ? t("auth.signIn") : t("auth.signInWithFeishu")}</span>
                </button>
              ) : (
                <div className="flex items-center gap-3 px-3 py-2.5 rounded-xl bg-zinc-900 border border-zinc-800">
                  <AvatarCircle user={user} size="sm" />
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-semibold text-zinc-200 truncate leading-none mb-1">{user.name}</p>
                    <p className="text-[10px] text-zinc-500 truncate font-mono">{user.email || ""}</p>
                  </div>
                  <button
                    onClick={logout}
                    className="p-3 rounded-md text-zinc-500 hover:text-red-400 hover:bg-red-400/10 transition-colors flex-shrink-0 active:scale-95"
                    title={t("auth.logout")}
                  >
                    <LogOut className="w-4 h-4" />
                  </button>
                </div>
              )}

              <div className="px-2 pb-1 text-[10px] tracking-wider text-zinc-600 flex justify-between items-center font-medium">
                <span>{CLUSTER_CONFIG.name}</span>
                <div className="flex items-center gap-1.5">
                  <Server className="w-3 h-3" />
                  <span>v0.1.0</span>
                </div>
              </div>
            </div>
          </aside>
        </div>
      )}
    </>
  );
}
