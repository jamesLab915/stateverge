import Link from "next/link";
import PowerTrendBadge from "@/components/power-trend-badge";
import { getPowerRowsFromDB } from "@/lib/db/queries";

export const dynamic = "force-dynamic";

function num(v: number | null | undefined, fallback = "—") {
  if (v == null || Number.isNaN(v)) return fallback;
  return String(v);
}

export default async function PowerPage() {
  const rows = await getPowerRowsFromDB();

  const withPower = rows.filter(
    (r) => r.score.power_score != null && !Number.isNaN(Number(r.score.power_score))
  );

  const rising = [...withPower]
    .filter((r) => r.power_trend === "rising")
    .sort(
      (a, b) =>
        (Number(b.power_delta) || 0) - (Number(a.power_delta) || 0)
    )
    .slice(0, 8);

  const highest = [...withPower]
    .sort(
      (a, b) =>
        Number(b.score.power_score ?? 0) - Number(a.score.power_score ?? 0)
    )
    .slice(0, 8);

  const fragile = [...withPower]
    .filter(
      (r) =>
        (r.score.power_classification || "").includes("Fragile") ||
        Number(r.score.risk) >= 58
    )
    .sort((a, b) => Number(b.score.risk) - Number(a.score.risk))
    .slice(0, 8);

  const opportunityLeaders = [...withPower]
    .sort(
      (a, b) =>
        Number(b.score.opportunity ?? 0) - Number(a.score.opportunity ?? 0)
    )
    .slice(0, 8);

  const volatile = [...withPower].sort((a, b) => {
    const da = Math.abs(Number(a.power_delta) ?? 0);
    const db = Math.abs(Number(b.power_delta) ?? 0);
    if (db !== da) return db - da;
    return Number(b.score.risk ?? 0) - Number(a.score.risk ?? 0);
  }).slice(0, 8);

  const trendCounts = withPower.reduce(
    (acc, r) => {
      const t = (r.power_trend || "stable").toLowerCase();
      if (t === "rising") acc.rising++;
      else if (t === "declining") acc.declining++;
      else acc.stable++;
      return acc;
    },
    { rising: 0, stable: 0, declining: 0 }
  );

  return (
    <main className="min-h-screen bg-white px-6 py-10 text-gray-900">
      <div className="mx-auto max-w-6xl">
        <p className="text-sm font-medium uppercase tracking-[0.2em] text-gray-500">
          Evolution Engine · Power board
        </p>
        <h1 className="mt-2 text-4xl font-bold tracking-tight">Global Power Board</h1>
        <p className="mt-3 max-w-3xl text-lg text-gray-600">
          Composite power (economy, innovation, infrastructure, governance, openness, future
          potential) with snapshot-linked trend — central to how StateVerge tracks
          structural momentum.
        </p>

        <div className="mt-8 flex flex-wrap gap-4">
          <Link
            href="/rankings"
            className="rounded-xl border border-gray-300 px-4 py-2 text-sm hover:bg-gray-50"
          >
            Rankings
          </Link>
          <Link
            href="/countries"
            className="rounded-xl bg-black px-4 py-2 text-sm text-white"
          >
            Countries
          </Link>
        </div>

        <section className="mt-12 rounded-2xl border border-gray-200 bg-slate-50 p-6">
          <h2 className="text-lg font-semibold">Power trend overview</h2>
          <p className="mt-1 text-sm text-gray-600">
            Distribution from latest snapshot per country (power_trend field).
          </p>
          <div className="mt-6 grid gap-4 sm:grid-cols-3">
            <div className="rounded-xl border border-white bg-white p-4 text-center shadow-sm">
              <p className="text-3xl font-bold text-emerald-700">{trendCounts.rising}</p>
              <p className="text-sm text-gray-600">Rising</p>
            </div>
            <div className="rounded-xl border border-white bg-white p-4 text-center shadow-sm">
              <p className="text-3xl font-bold text-gray-800">{trendCounts.stable}</p>
              <p className="text-sm text-gray-600">Stable</p>
            </div>
            <div className="rounded-xl border border-white bg-white p-4 text-center shadow-sm">
              <p className="text-3xl font-bold text-rose-700">{trendCounts.declining}</p>
              <p className="text-sm text-gray-600">Declining</p>
            </div>
          </div>
        </section>

        <div className="mt-14 grid gap-8 lg:grid-cols-3">
          <section className="rounded-2xl border border-gray-200 p-6">
            <h2 className="text-lg font-semibold">Fastest Rising Powers</h2>
            <p className="mt-1 text-sm text-gray-500">By power_delta (last snapshot)</p>
            <ul className="mt-4 space-y-3 text-sm">
              {rising.length === 0 ? (
                <li className="text-gray-500">No rising trends yet.</li>
              ) : (
                rising.map((r) => (
                  <li key={r.code} className="flex items-center justify-between gap-2">
                    <Link href={`/countries/${r.code}`} className="font-medium hover:underline">
                      {r.name}
                    </Link>
                    <span className="text-emerald-700">+{num(r.power_delta)}</span>
                  </li>
                ))
              )}
            </ul>
          </section>

          <section className="rounded-2xl border border-gray-200 p-6">
            <h2 className="text-lg font-semibold">Most Fragile Powers</h2>
            <p className="mt-1 text-sm text-gray-500">
              Fragile classification or risk ≥ 58, sorted by risk
            </p>
            <ul className="mt-4 space-y-3 text-sm">
              {fragile.length === 0 ? (
                <li className="text-gray-500">No fragile signals in current slice.</li>
              ) : (
                fragile.map((r) => (
                  <li key={r.code} className="flex items-center justify-between gap-2">
                    <Link href={`/countries/${r.code}`} className="font-medium hover:underline">
                      {r.name}
                    </Link>
                    <span className="text-rose-700">R {r.score.risk}</span>
                  </li>
                ))
              )}
            </ul>
          </section>

          <section className="rounded-2xl border border-gray-200 p-6">
            <h2 className="text-lg font-semibold">Highest Opportunity Powers</h2>
            <p className="mt-1 text-sm text-gray-500">By opportunity score</p>
            <ul className="mt-4 space-y-3 text-sm">
              {opportunityLeaders.map((r) => (
                <li key={r.code} className="flex items-center justify-between gap-2">
                  <Link href={`/countries/${r.code}`} className="font-medium hover:underline">
                    {r.name}
                  </Link>
                  <span className="font-semibold text-indigo-700">
                    {r.score.opportunity}
                  </span>
                </li>
              ))}
            </ul>
          </section>
        </div>

        <div className="mt-8 grid gap-8 lg:grid-cols-2">
          <section className="rounded-2xl border border-gray-200 p-6">
            <h2 className="text-lg font-semibold">Highest Power Score</h2>
            <ul className="mt-4 space-y-3 text-sm">
              {highest.map((r) => (
                <li key={r.code} className="flex items-center justify-between gap-2">
                  <Link href={`/countries/${r.code}`} className="font-medium hover:underline">
                    {r.name}
                  </Link>
                  <span className="font-semibold">{num(r.score.power_score)}</span>
                </li>
              ))}
            </ul>
          </section>

          <section className="rounded-2xl border border-gray-200 p-6">
            <h2 className="text-lg font-semibold">Most Volatile Powers</h2>
            <p className="mt-1 text-sm text-gray-500">|power_delta| then risk</p>
            <ul className="mt-4 space-y-3 text-sm">
              {volatile.map((r) => (
                <li key={r.code} className="flex items-center justify-between gap-2">
                  <Link href={`/countries/${r.code}`} className="font-medium hover:underline">
                    {r.name}
                  </Link>
                  <span className="text-gray-700">
                    |Δ| {num(r.power_delta != null ? Math.abs(r.power_delta) : null)} · R{" "}
                    {r.score.risk}
                  </span>
                </li>
              ))}
            </ul>
          </section>
        </div>

        <section className="mt-14">
          <h2 className="text-xl font-semibold">Power Ranking</h2>
          <p className="mt-1 text-sm text-gray-500">
            power_score · classification · trend tag
          </p>

          <div className="mt-6 overflow-hidden rounded-2xl border border-gray-200">
            <table className="min-w-full text-left text-sm">
              <thead className="bg-gray-50 text-gray-600">
                <tr>
                  <th className="px-6 py-4">Rank</th>
                  <th className="px-6 py-4">Country</th>
                  <th className="px-6 py-4">Region</th>
                  <th className="px-6 py-4">Power</th>
                  <th className="px-6 py-4">Δ</th>
                  <th className="px-6 py-4">Trend</th>
                  <th className="px-6 py-4">Classification</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200">
                {withPower.map((r, i) => (
                  <tr key={r.code}>
                    <td className="px-6 py-4 font-medium">{i + 1}</td>
                    <td className="px-6 py-4">
                      <Link
                        href={`/countries/${r.code}`}
                        className="font-medium hover:underline"
                      >
                        {r.name}
                      </Link>
                    </td>
                    <td className="px-6 py-4 text-gray-600">{r.region}</td>
                    <td className="px-6 py-4 font-semibold">
                      {num(r.score.power_score)}
                    </td>
                    <td className="px-6 py-4 text-gray-700">{num(r.power_delta)}</td>
                    <td className="px-6 py-4">
                      <PowerTrendBadge trend={r.power_trend} />
                    </td>
                    <td className="px-6 py-4 text-gray-800">
                      {r.score.power_classification ?? "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </main>
  );
}
