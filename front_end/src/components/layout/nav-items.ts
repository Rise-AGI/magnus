// front_end/src/components/layout/nav-items.ts
// 桌面 sidebar 与移动 drawer 共用的导航条目，避免两份 NAV_ITEMS 漂移。
import {
  ArrowRight,
  Container,
  Dna,
  FolderSync,
  Layers,
  LayoutDashboard,
  MessageCircle,
  Rocket,
  ScrollText,
  Users,
} from "lucide-react";


export interface NavItem {
  i18nKey: string;
  href: string;
  icon: typeof ArrowRight;
  wip?: boolean;
}


export const NAV_ITEMS: NavItem[] = [
  { i18nKey: "nav.chat", href: "/chat", icon: MessageCircle },
  { i18nKey: "nav.tools", href: "/tools", icon: FolderSync },
  { i18nKey: "nav.explorer", href: "/explorer", icon: ArrowRight, wip: true },
  { i18nKey: "nav.people", href: "/people", icon: Users },
  { i18nKey: "nav.jobs", href: "/jobs", icon: Rocket },
  { i18nKey: "nav.blueprints", href: "/blueprints", icon: ScrollText },
  { i18nKey: "nav.services", href: "/services", icon: Layers },
  { i18nKey: "nav.skills", href: "/skills", icon: Dna },
  { i18nKey: "nav.images", href: "/images", icon: Container },
  { i18nKey: "nav.cluster", href: "/cluster", icon: LayoutDashboard },
];
