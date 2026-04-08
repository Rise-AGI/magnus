// front_end/src/components/jobs/metrics-chart.tsx
"use client";

import React, { useState, useEffect, useCallback, useMemo } from "react";
import {
  ResponsiveContainer, LineChart, Line, XAxis, YAxis, Tooltip, CartesianGrid,
} from "recharts";
import { client } from "@/lib/api";
import { POLL_INTERVAL } from "@/lib/config";
import { useLanguage } from "@/context/language-context";
import { BarChart3 } from "lucide-react";
import { SearchableSelect } from "@/components/ui/searchable-select";


interface MetricStream {
  name: string;
  kind: string;
  unit: string | null;
  step_domain: string | null;
  labels: Record<string, string>;
  point_count: number;
}

interface MetricPoint {
  value: number;
  time_unix_ms: number;
  step?: number;
  labels?: Record<string, string>;
}

interface QueryResult {
  name: string;
  points: MetricPoint[];
}

const STREAM_COLORS = [
  "#60a5fa", "#34d399", "#fbbf24", "#f87171", "#a78bfa",
  "#2dd4bf", "#fb923c", "#e879f9", "#38bdf8", "#4ade80",
];

function labelsKey(labels: Record<string, string>): string {
  return Object.entries(labels).sort().map(([k, v]) => `${k}=${v}`).join(",");
}

function formatTime(ms: number): string {
  const d = new Date(ms);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function formatValue(v: number, unit: string | null): string {
  if (unit === "bytes") {
    if (v >= 1073741824) return `${(v / 1073741824).toFixed(1)} GiB`;
    if (v >= 1048576) return `${(v / 1048576).toFixed(0)} MiB`;
    return `${v.toFixed(0)} B`;
  }
  if (unit === "percent") return `${v.toFixed(1)}%`;
  if (Math.abs(v) >= 1e6) return v.toExponential(2);
  if (Math.abs(v) < 0.01 && v !== 0) return v.toExponential(2);
  return v.toFixed(v % 1 === 0 ? 0 : 3);
}

function groupStreamsByMetric(streams: MetricStream[]): Map<string, MetricStream[]> {
  const map = new Map<string, MetricStream[]>();
  for (const s of streams) {
    const existing = map.get(s.name) || [];
    existing.push(s);
    map.set(s.name, existing);
  }
  return map;
}

function metricDisplayName(name: string): string {
  return name.split(".").map(s => s.charAt(0).toUpperCase() + s.slice(1)).join(" › ");
}


export function MetricsChart({ jobId, jobStatus }: { jobId: string; jobStatus: string }) {
  const { t } = useLanguage();
  const [streams, setStreams] = useState<MetricStream[]>([]);
  const [selectedMetric, setSelectedMetric] = useState<string | null>(null);
  const [data, setData] = useState<Map<string, QueryResult>>(new Map());
  const [loading, setLoading] = useState(true);

  const fetchStreams = useCallback(async () => {
    try {
      const result: MetricStream[] = await client(`/api/jobs/${jobId}/metrics/streams`);
      setStreams(result);
      if (result.length > 0 && selectedMetric === null) {
        setSelectedMetric(result[0].name);
      }
    } catch {
      // fail silently
    } finally {
      setLoading(false);
    }
  }, [jobId, selectedMetric]);

  const fetchData = useCallback(async (metricName: string, metricStreams: MetricStream[]) => {
    const entries = await Promise.all(
      metricStreams.map(async (stream): Promise<[string, QueryResult] | null> => {
        try {
          const labelsParam = Object.keys(stream.labels).length > 0
            ? `&labels=${encodeURIComponent(JSON.stringify(stream.labels))}` : "";
          const stepParam = stream.step_domain ? `&step_domain=${stream.step_domain}` : "";
          const result: QueryResult = await client(
            `/api/jobs/${jobId}/metrics/query?name=${encodeURIComponent(metricName)}${labelsParam}${stepParam}`
          );
          return [labelsKey(stream.labels), result];
        } catch {
          return null;
        }
      })
    );
    const results = new Map<string, QueryResult>();
    for (const entry of entries) {
      if (entry) results.set(entry[0], entry[1]);
    }
    setData(results);
  }, [jobId]);

  const grouped = useMemo(() => groupStreamsByMetric(streams), [streams]);

  const selectedStreams = useMemo(
    () => (selectedMetric ? grouped.get(selectedMetric) || [] : []),
    [grouped, selectedMetric],
  );

  const selectedUnit = selectedStreams[0]?.unit ?? null;

  useEffect(() => {
    fetchStreams();
  }, [fetchStreams]);

  useEffect(() => {
    if (!selectedMetric || selectedStreams.length === 0) return;
    fetchData(selectedMetric, selectedStreams);
  }, [selectedMetric, selectedStreams, fetchData]);

  useEffect(() => {
    if (jobStatus !== "Running") return;
    const interval = setInterval(() => {
      fetchStreams();
      if (selectedMetric && selectedStreams.length > 0) {
        fetchData(selectedMetric, selectedStreams);
      }
    }, POLL_INTERVAL * 5);
    return () => clearInterval(interval);
  }, [jobStatus, fetchStreams, fetchData, selectedMetric, selectedStreams]);

  const chartData = useMemo(() => {
    const timeMap = new Map<number, Record<string, number>>();
    const seriesKeys: string[] = [];

    for (const [key, result] of Array.from(data.entries())) {
      const seriesName = key || "default";
      if (!seriesKeys.includes(seriesName)) seriesKeys.push(seriesName);
      for (const point of result.points) {
        const existing = timeMap.get(point.time_unix_ms) || {};
        existing[seriesName] = point.value;
        timeMap.set(point.time_unix_ms, existing);
      }
    }

    const sorted = Array.from(timeMap.entries())
      .sort(([a], [b]) => a - b)
      .map(([time, values]) => ({ time, ...values }));

    return { rows: sorted, seriesKeys };
  }, [data]);

  const metricNames = useMemo(() => {
    const systemMetrics: string[] = [];
    const trainMetrics: string[] = [];
    const otherMetrics: string[] = [];
    for (const name of Array.from(grouped.keys())) {
      if (name.startsWith("system.")) systemMetrics.push(name);
      else if (name.startsWith("train.") || name.startsWith("eval.")) trainMetrics.push(name);
      else otherMetrics.push(name);
    }
    return { systemMetrics, trainMetrics, otherMetrics };
  }, [grouped]);

  const selectOptions = useMemo(() => {
    const opts: { label: string; value: string; meta?: string }[] = [];
    const addGroup = (groupLabel: string, names: string[]) => {
      for (const name of names) {
        opts.push({ label: metricDisplayName(name), value: name, meta: groupLabel });
      }
    };
    addGroup(t("jobDetail.metricsSystem"), metricNames.systemMetrics);
    addGroup(t("jobDetail.metricsTraining"), metricNames.trainMetrics);
    addGroup(t("jobDetail.metricsOther"), metricNames.otherMetrics);
    return opts;
  }, [metricNames, t]);

  if (loading) {
    return (
      <div className="h-full flex items-center justify-center text-zinc-500">
        <span className="animate-pulse">{t("jobDetail.metricsLoading")}</span>
      </div>
    );
  }

  if (streams.length === 0) {
    return (
      <div className="h-full flex flex-col items-center justify-center text-zinc-500 gap-4 min-h-[400px]">
        <BarChart3 className="w-12 h-12 opacity-20" />
        <div className="text-center">
          <p className="text-zinc-200 font-bold text-lg mb-1">{t("jobDetail.metricsNoData")}</p>
          <p className="text-zinc-500 text-sm max-w-md">{t("jobDetail.metricsNoDataDesc")}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col justify-center">
      {/* Metric selector */}
      <div className="shrink-0 mb-4">
        <SearchableSelect
          value={selectedMetric ?? ""}
          options={selectOptions}
          onChange={(val) => setSelectedMetric(val)}
          placeholder={t("jobDetail.metricsSelectStream")}
        />
        {selectedMetric && chartData.seriesKeys.length > 1 && (
          <div className="flex gap-3 mt-2 flex-wrap">
            {chartData.seriesKeys.map((key, i) => (
              <span key={key} className="text-xs text-zinc-500 flex items-center gap-1">
                <span className="w-2 h-2 rounded-full" style={{ backgroundColor: STREAM_COLORS[i % STREAM_COLORS.length] }} />
                {key}
              </span>
            ))}
          </div>
        )}
      </div>

      {/* Chart */}
      <div className="shrink-0 h-[375px]">
        {chartData.rows.length > 0 ? (
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={chartData.rows} margin={{ top: 5, right: 20, bottom: 5, left: 10 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#27272a" />
              <XAxis
                dataKey="time"
                type="number"
                domain={["dataMin", "dataMax"]}
                tickFormatter={formatTime}
                stroke="#52525b"
                tick={{ fontSize: 11, fill: "#71717a" }}
              />
              <YAxis
                stroke="#52525b"
                tick={{ fontSize: 11, fill: "#71717a" }}
                tickFormatter={(v: number) => formatValue(v, selectedUnit)}
                width={65}
              />
              <Tooltip
                contentStyle={{
                  backgroundColor: "#18181b",
                  border: "1px solid #3f3f46",
                  borderRadius: "6px",
                  fontSize: "12px",
                }}
                labelFormatter={(label) => formatTime(Number(label))}
                formatter={(value) => [formatValue(Number(value), selectedUnit), ""]}
              />
              {chartData.seriesKeys.map((key, i) => (
                <Line
                  key={key}
                  type="monotone"
                  dataKey={key}
                  stroke={STREAM_COLORS[i % STREAM_COLORS.length]}
                  dot={false}
                  strokeWidth={1.5}
                  isAnimationActive={false}
                  connectNulls
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        ) : (
          <div className="h-full flex items-center justify-center text-zinc-600 text-sm">
            {t("jobDetail.metricsNoData")}
          </div>
        )}
      </div>
    </div>
  );
}
