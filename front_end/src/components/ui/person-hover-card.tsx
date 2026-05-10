// front_end/src/components/ui/person-hover-card.tsx
"use client";

import { useState, useCallback } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import * as Popover from "@radix-ui/react-popover";
import {
  Shield,
  Box,
  DraftingCompass,
  Activity,
  Dna,
  Container,
  ExternalLink,
  MessageCircle,
  Loader2,
} from "lucide-react";
import { client } from "@/lib/api";
import { useLanguage } from "@/context/language-context";
import { useAuth } from "@/context/auth-context";
import { formatBeijingTime } from "@/lib/utils";
import type { UserDetail } from "@/types/auth";
import { AvatarCircle } from "./user-avatar";


/** 头像 / 名字渲染时附带的"warm"信息，open 之前先用，open 后被 fetch 到的 detail 覆盖 */
export interface PersonHoverCardWarmData {
  name: string;
  avatar_url?: string | null;
}


interface PersonHoverCardProps {
  userId: string;
  warm?: PersonHoverCardWarmData;
  children: React.ReactNode;
  align?: "start" | "center" | "end";
  side?: "top" | "right" | "bottom" | "left";
}


/**
 * 点击头像 / 名字 -> 弹出"这个人是谁"轻量浮卡。
 *
 * 设计语义：
 * - 触发即 lazy fetch /api/users/{id}，失败时 fall-back 到 warm 数据；
 * - 5 个 entity 计数 chip 直接是 link，去对应列表页带 ?owner_id 筛选 —— 这是
 *   主探索路径（"看 alice 最近在跑什么"）；
 * - 两个 action：去 People 页 / 私信。社交不是热链路。
 *
 * 不内置头像渲染：children 自己负责长什么样，HoverCard 只承担"点开 -> 看人"语义。
 * 这样 TransferableAuthor、AvatarCircle 各种现有皮肤都能直接套。
 *
 * 上级展示是纯静态：avatar + 名字。原本递归套 PersonHoverCard 可顺链上看，但视觉
 * 像"套娃"，且组织链一般不深，需要时走"去人事"按钮即可。
 */
export function PersonHoverCard({
  userId,
  warm,
  children,
  align = "start",
  side = "bottom",
}: PersonHoverCardProps) {
  const { t } = useLanguage();
  const router = useRouter();
  const { user: currentUser } = useAuth();

  const [detail, setDetail] = useState<UserDetail | null>(null);
  const [fetching, setFetching] = useState(false);
  const [fetchError, setFetchError] = useState(false);
  const [hasFetched, setHasFetched] = useState(false);

  const [open, setOpen] = useState(false);
  const [isCreatingDm, setIsCreatingDm] = useState(false);

  const isSelf = currentUser?.id === userId;

  const handleOpenChange = useCallback((next: boolean) => {
    setOpen(next);
    if (next && !hasFetched) {
      setFetching(true);
      setFetchError(false);
      client(`/api/users/${userId}`)
        .then((res: UserDetail) => setDetail(res))
        .catch(() => setFetchError(true))
        .finally(() => {
          setFetching(false);
          setHasFetched(true);
        });
    }
  }, [hasFetched, userId]);

  const handleDirectMessage = async () => {
    if (isSelf || isCreatingDm) return;
    setIsCreatingDm(true);
    try {
      const conv = await client("/api/conversations", {
        json: { type: "p2p", member_ids: [userId] },
      });
      setOpen(false);
      router.push(`/chat/${conv.id}`);
    } catch (e) {
      console.error("Failed to create DM conversation", e);
    } finally {
      setIsCreatingDm(false);
    }
  };

  const handleOpenInPeople = () => {
    setOpen(false);
    router.push(`/people?focus=${userId}`);
  };

  // 卡片头部 fall-back 数据：detail 优先，warm 次之
  const headName = detail?.name ?? warm?.name ?? "";
  const headAvatarUrl = detail?.avatar_url ?? warm?.avatar_url ?? null;

  // modal=true：浮卡打开期间外部 pointer-events 屏蔽。一次点击只关浮卡、不
  // 同时触发外部祖先 onClick（如 jobs-table 行的 router.push 导航）。也提供
  // 焦点 trap 与 ESC 关闭。
  return (
    <Popover.Root open={open} onOpenChange={handleOpenChange} modal>
      <Popover.Trigger asChild>
        {/*
         * button 兼任两个职责：
         * 1) stopPropagation 阻断 click 冒泡到外层（行点击导航 / 行删除等）；
         * 2) hover:bg-zinc-800/50 + 半透明方框是 Magnus 既有的"可点击"hover 反馈
         *    样式。-m-1.5 p-1.5 让方框向外微扩 6px 但不撑开布局，统一所有挂载位
         *    的视觉。callsite 内层不要再叠 hover-bg，否则颜色会复合变深。
         */}
        <button
          type="button"
          onClick={(e) => e.stopPropagation()}
          className="inline-flex items-center text-left cursor-pointer focus:outline-none rounded-lg -m-1.5 p-1.5 transition-colors hover:bg-zinc-800/50"
        >
          {children}
        </button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          align={align}
          side={side}
          sideOffset={8}
          collisionPadding={12}
          // React 合成事件穿透 Portal 沿组件树冒泡：浮卡内 chip/button click
          // 否则会触发祖先（如 jobs-table 行的 router.push）的 onClick，劫持导航
          onClick={(e) => e.stopPropagation()}
          className="w-[320px] bg-[#0A0A0C] border border-zinc-800 rounded-xl shadow-2xl z-[210] overflow-hidden ring-1 ring-white/5 animate-in fade-in zoom-in-95 duration-100"
        >
          {/* Header: avatar + name + admin badge */}
          <div className="px-5 pt-5 pb-4 flex items-center gap-4">
            <AvatarCircle
              user={{ name: headName, avatar_url: headAvatarUrl }}
              size="lg"
            />
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-base font-bold text-zinc-100 tracking-tight truncate">
                  {headName || "—"}
                </span>
                {detail?.is_admin && (
                  <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-amber-900/30 text-amber-400 border border-amber-800/50">
                    <Shield className="w-2.5 h-2.5" />
                    {t("people.role.admin")}
                  </span>
                )}
              </div>
              {detail && (
                <div className="mt-1 text-[11px] text-zinc-600 font-mono">
                  {t("people.drawer.created")} · {formatBeijingTime(detail.created_at)}
                </div>
              )}
            </div>
          </div>

          {/* Leader：纯静态展示 avatar + 名字。组织链一般不深，需要继续看上级走"去人事"按钮。 */}
          <div className="px-5 py-3 border-t border-zinc-800/70 flex items-center gap-3">
            <span className="text-[10px] uppercase tracking-wider text-zinc-500 font-bold shrink-0">
              {t("people.table.leader")}
            </span>
            {detail?.parent_name ? (
              <div className="inline-flex items-center gap-2 min-w-0">
                <AvatarCircle
                  user={{
                    name: detail.parent_name,
                    avatar_url: detail.parent_avatar_url ?? null,
                  }}
                  size="sm"
                />
                <span className="text-sm font-medium text-zinc-200 truncate">
                  {detail.parent_name}
                </span>
              </div>
            ) : fetching ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin text-zinc-600" />
            ) : fetchError ? (
              <span className="text-sm text-zinc-700 italic">—</span>
            ) : (
              <span className="text-sm text-zinc-700 italic">
                {detail ? t("people.leader.void") : ""}
              </span>
            )}
          </div>

          {/* Entity counts: 5 chips, 每个是去对应列表页带 owner_id 的 link */}
          <div className="px-5 py-3 border-t border-zinc-800/70 grid grid-cols-5 gap-1.5">
            <CountChip
              href={`/jobs?owner_id=${userId}`}
              icon={<Box className="w-3.5 h-3.5" />}
              count={detail?.job_count}
              label={t("personCard.jobs")}
              onNavigate={() => setOpen(false)}
              tone="zinc"
              fetching={fetching}
              fetchError={fetchError}
            />
            <CountChip
              href={`/blueprints?owner_id=${userId}`}
              icon={<DraftingCompass className="w-3.5 h-3.5" />}
              count={detail?.blueprint_count}
              label={t("personCard.blueprints")}
              onNavigate={() => setOpen(false)}
              tone="blue"
              fetching={fetching}
              fetchError={fetchError}
            />
            <CountChip
              href={`/services?owner_id=${userId}`}
              icon={<Activity className="w-3.5 h-3.5" />}
              count={detail?.service_count}
              label={t("personCard.services")}
              onNavigate={() => setOpen(false)}
              tone="teal"
              fetching={fetching}
              fetchError={fetchError}
            />
            <CountChip
              href={`/skills?owner_id=${userId}`}
              icon={<Dna className="w-3.5 h-3.5" />}
              count={detail?.skill_count}
              label={t("personCard.skills")}
              onNavigate={() => setOpen(false)}
              tone="violet"
              fetching={fetching}
              fetchError={fetchError}
            />
            <CountChip
              href={`/images?owner_id=${userId}`}
              icon={<Container className="w-3.5 h-3.5" />}
              count={detail?.image_count}
              label={t("personCard.images")}
              onNavigate={() => setOpen(false)}
              tone="zinc"
              fetching={fetching}
              fetchError={fetchError}
            />
          </div>

          {/* Actions */}
          <div className="border-t border-zinc-800/70 bg-zinc-900/40 px-3 py-2.5 flex items-center gap-1.5">
            <ActionButton
              onClick={handleOpenInPeople}
              icon={<ExternalLink className="w-3.5 h-3.5" />}
              label={t("personCard.openInPeople")}
            />
            {!isSelf && (
              <ActionButton
                onClick={handleDirectMessage}
                icon={isCreatingDm ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <MessageCircle className="w-3.5 h-3.5" />}
                label={t("chat.directMessage")}
                disabled={isCreatingDm}
                tone="blue"
              />
            )}
          </div>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}


// ─── 内部小组件 ───────────────────────────────────────────────────────────

type ChipTone = "zinc" | "blue" | "teal" | "violet";

const CHIP_TONE: Record<ChipTone, string> = {
  zinc: "text-zinc-300 hover:bg-zinc-800/60 border-zinc-800/60",
  blue: "text-blue-300 hover:bg-blue-900/30 border-blue-900/40",
  teal: "text-teal-300 hover:bg-teal-900/30 border-teal-900/40",
  violet: "text-violet-300 hover:bg-violet-900/30 border-violet-900/40",
};

function CountChip({
  href,
  icon,
  count,
  label,
  onNavigate,
  tone,
  fetching,
  fetchError,
}: {
  href: string;
  icon: React.ReactNode;
  count: number | undefined;
  label: string;
  onNavigate: () => void;
  tone: ChipTone;
  fetching: boolean;
  fetchError: boolean;
}) {
  // count==null 时区分三态：fetching 中点占位 / 取数失败显 — / 取数完成但真为 0
  const display = count != null
    ? count
    : fetching
      ? "·"
      : fetchError
        ? "—"
        : "0";
  return (
    <Link
      href={href}
      onClick={onNavigate}
      title={label}
      className={`flex flex-col items-center gap-0.5 py-2 rounded-lg border bg-zinc-900/40 transition-colors ${CHIP_TONE[tone]}`}
    >
      <span className="text-zinc-500">{icon}</span>
      <span className="text-sm font-bold tabular-nums leading-none">{display}</span>
      <span className="text-[9px] uppercase tracking-wider text-zinc-600 leading-none">
        {label}
      </span>
    </Link>
  );
}


type ActionTone = "neutral" | "blue";

const ACTION_TONE: Record<ActionTone, string> = {
  neutral: "text-zinc-300 hover:bg-zinc-800 hover:text-white border-zinc-700/50",
  blue: "text-blue-300 hover:bg-blue-600/30 hover:text-blue-200 border-blue-700/40",
};

function ActionButton({
  onClick,
  icon,
  label,
  disabled,
  tone = "neutral",
}: {
  onClick: () => void;
  icon: React.ReactNode;
  label: string;
  disabled?: boolean;
  tone?: ActionTone;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={`flex-1 flex items-center justify-center gap-1.5 px-2 py-1.5 rounded-md text-xs font-medium border bg-zinc-900/40 transition-colors disabled:opacity-50 ${ACTION_TONE[tone]}`}
    >
      {icon}
      <span className="truncate">{label}</span>
    </button>
  );
}
