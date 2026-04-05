import Link from "next/link";
import { getAllActorsList } from "@/lib/db/usFederalQueries";

export const dynamic = "force-dynamic";

export default async function USFederalActorsPage() {
  let actors: Awaited<ReturnType<typeof getAllActorsList>> = [];
  let err: string | null = null;
  try {
    actors = await getAllActorsList();
  } catch (e) {
    err = e instanceof Error ? e.message : "Error";
  }

  if (err) {
    return (
      <main className="px-6 py-12">
        <p className="text-red-600">{err}</p>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-white px-6 py-10">
      <div className="mx-auto max-w-6xl">
        <h1 className="text-3xl font-bold">Federal actors</h1>
        <p className="mt-2 text-sm text-gray-600">
          Formal power = position weight. X signal column reserved (auxiliary only).
        </p>

        <div className="mt-8 overflow-x-auto rounded-2xl border border-gray-200">
          <table className="min-w-full text-left text-sm">
            <thead className="bg-gray-50 text-gray-600">
              <tr>
                <th className="px-4 py-3">Name</th>
                <th className="px-4 py-3">Office</th>
                <th className="px-4 py-3">Branch</th>
                <th className="px-4 py-3">Dept</th>
                <th className="px-4 py-3">Formal</th>
                <th className="px-4 py-3">Influence</th>
                <th className="px-4 py-3">Conflict</th>
                <th className="px-4 py-3">X signal</th>
                <th className="px-4 py-3">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {actors.map((a) => (
                <tr key={a.id} className={a.is_active ? "" : "opacity-60"}>
                  <td className="px-4 py-3">
                    <Link href={`/us/actors/${a.slug}`} className="font-medium hover:underline">
                      {a.name}
                    </Link>
                  </td>
                  <td className="px-4 py-3 text-gray-700">{a.office_title ?? "—"}</td>
                  <td className="px-4 py-3">{a.branch ?? "—"}</td>
                  <td className="px-4 py-3 text-gray-600">{a.department ?? "—"}</td>
                  <td className="px-4 py-3 font-semibold">{a.formal_power_score ?? "—"}</td>
                  <td className="px-4 py-3">{a.political_influence_score ?? "—"}</td>
                  <td className="px-4 py-3 text-rose-700">{a.conflict_index ?? "—"}</td>
                  <td className="px-4 py-3 text-gray-400">
                    {a.x_signal_score != null ? a.x_signal_score : "—"}
                  </td>
                  <td className="px-4 py-3 text-xs">{a.power_status ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </main>
  );
}
