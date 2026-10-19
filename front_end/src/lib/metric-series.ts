export function metricLabelsKey(labels: Record<string, string>): string {
  return JSON.stringify(Object.entries(labels).sort(([a], [b]) => a.localeCompare(b)));
}

export function metricSeriesName(name: string, labels: Record<string, string>): string {
  if (!name.startsWith("system.")) {
    return Object.entries(labels).sort().map(([key, value]) => `${key}=${value}`).join(", ");
  }

  const identities = ["node", "host", "hostname", "device", "gpu", "gpu_index", "rank", "local_rank"];
  return identities.flatMap(key => {
    const value = labels[key];
    if (value == null || value === "") return [];
    return [key === "rank" || key === "local_rank" || key === "gpu_index"
      ? `${key.replaceAll("_", " ")} ${value}`
      : value];
  }).join(" · ");
}
