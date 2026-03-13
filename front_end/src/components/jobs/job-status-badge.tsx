// front_end/src/components/jobs/job-status-badge.tsx
import { Play, CheckCircle2, AlertCircle, PauseCircle, Clock, Ban, Loader2 } from "lucide-react";
import { useLanguage } from "@/context/language-context";

interface JobStatusBadgeProps {
  status: string;
  size?: "sm" | "md";
  animate?: boolean;
}

export function JobStatusBadge({ status, size = "sm", animate = true }: JobStatusBadgeProps) {
  const { t } = useLanguage();

  const config = {
    Running:    { color: "text-blue-400", bg: "bg-blue-500/10", border: "border-blue-500/20", icon: Play },
    Success:    { color: "text-green-400", bg: "bg-green-500/10", border: "border-green-500/20", icon: CheckCircle2 },
    Failed:     { color: "text-red-400", bg: "bg-red-500/10", border: "border-red-500/20", icon: AlertCircle },
    Paused:     { color: "text-orange-400", bg: "bg-orange-500/10", border: "border-orange-500/20", icon: PauseCircle },
    Pending:    { color: "text-yellow-400", bg: "bg-yellow-500/10", border: "border-yellow-500/20", icon: Clock },
    Preparing:  { color: "text-cyan-400", bg: "bg-cyan-500/10", border: "border-cyan-500/20", icon: Loader2 },
    Terminated: { color: "text-zinc-400", bg: "bg-zinc-500/10", border: "border-zinc-500/20", icon: Ban },
  // @ts-ignore
  }[status] || { color: "text-zinc-400", bg: "bg-zinc-800", border: "border-zinc-700", icon: Clock };

  const Icon = config.icon;
  // status 是运行时动态值，无法满足 TranslationKey 字面量类型
  const displayText = t(`jobStatus.${status.toLowerCase()}` as any) || status;

  if (size === "md") {
    const shouldAnimate = animate && (status === 'Running' || status === 'Preparing');
    return <Icon className={`w-5 h-5 ${config.color} ${shouldAnimate ? 'animate-pulse' : ''}`} />;
  }

  return (
    <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-semibold border shadow-sm ${config.bg} ${config.color} ${config.border}`}>
      {(status === 'Running' || status === 'Preparing') && animate && (
        <span className="relative flex h-1.5 w-1.5">
          <span className="animate-ping absolute inline-flex h-full w-full rounded-full opacity-75 bg-current"></span>
          <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-current"></span>
        </span>
      )}
      {displayText}
    </span>
  );
}
