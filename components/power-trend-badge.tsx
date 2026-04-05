import type { PowerTrend } from "@/lib/power";

export default function PowerTrendBadge({
  trend,
}: {
  trend: string | null | undefined;
}) {
  const t = (trend || "stable").toLowerCase();
  const cls =
    t === "rising"
      ? "bg-emerald-100 text-emerald-800"
      : t === "declining"
        ? "bg-rose-100 text-rose-800"
        : "bg-gray-100 text-gray-700";

  return (
    <span className={`rounded-full px-3 py-1 text-xs font-medium capitalize ${cls}`}>
      {t}
    </span>
  );
}

export function normalizePowerTrend(t: string | null | undefined): PowerTrend {
  const x = (t || "stable").toLowerCase();
  if (x === "rising" || x === "declining") return x;
  return "stable";
}
