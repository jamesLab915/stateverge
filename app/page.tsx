import Link from "next/link";
import { getCountryCards } from "@/lib/data";

export default function HomePage() {
  const countries = getCountryCards();

  const totalCountries = countries.length;

  const averageScore =
    Math.round(
      countries.reduce((sum, c) => sum + (c.score?.overall ?? 0), 0) /
        totalCountries
    ) || 0;

  const highestOpportunityCountry =
    countries.reduce((best, current) => {
      const bestValue = best.score?.opportunity ?? 0;
      const currentValue = current.score?.opportunity ?? 0;
      return currentValue > bestValue ? current : best;
    }, countries[0])?.name || "-";

  return (
    <main className="min-h-screen bg-white text-gray-900">
      <section className="px-6 py-16">
        <div className="mx-auto max-w-6xl">
          <p className="text-sm font-medium uppercase tracking-[0.2em] text-gray-500">
            Global country intelligence
          </p>

          <h1 className="mt-4 max-w-4xl text-5xl font-bold tracking-tight">
            Understand Countries as Systems, Not Headlines.
          </h1>

          <p className="mt-6 max-w-3xl text-lg leading-8 text-gray-600">
            StateVerge turns countries into structured intelligence profiles:
            governance, economy, risk, opportunity, and future potential.
          </p>

          <div className="mt-10 flex flex-wrap gap-4">
            <Link
              href="/countries"
              className="rounded-xl bg-black px-5 py-3 text-white"
            >
              Explore Countries
            </Link>

            <Link
              href="/rankings"
              className="rounded-xl border border-gray-300 px-5 py-3 text-gray-900"
            >
              View Rankings
            </Link>
          </div>

          <div className="mt-14 grid gap-6 md:grid-cols-3">
            <Link
              href="/countries"
              className="rounded-2xl border border-gray-200 p-6 shadow-sm transition hover:shadow-md"
            >
              <h2 className="text-xl font-semibold">Country Profiles</h2>
              <p className="mt-3 text-sm leading-6 text-gray-600">
                Browse structured country pages with key metrics, summaries, risk, and opportunity.
              </p>
            </Link>

            <Link
              href="/rankings"
              className="rounded-2xl border border-gray-200 p-6 shadow-sm transition hover:shadow-md"
            >
              <h2 className="text-xl font-semibold">Rankings</h2>
              <p className="mt-3 text-sm leading-6 text-gray-600">
                Compare countries by overall score, risk profile, and opportunity level.
              </p>
            </Link>

            <Link
              href="/compare/us-cn"
              className="rounded-2xl border border-gray-200 p-6 shadow-sm transition hover:shadow-md"
            >
              <h2 className="text-xl font-semibold">Head-to-Head Compare</h2>
              <p className="mt-3 text-sm leading-6 text-gray-600">
                View two countries side by side and generate a quick structural comparison.
              </p>
            </Link>
          </div>

          <div className="mt-16 grid gap-6 md:grid-cols-3">
            <div className="rounded-2xl border border-gray-200 p-6">
              <p className="text-sm text-gray-500">Countries Tracked</p>
              <p className="mt-2 text-3xl font-bold">{totalCountries}</p>
            </div>

            <div className="rounded-2xl border border-gray-200 p-6">
              <p className="text-sm text-gray-500">Average Score</p>
              <p className="mt-2 text-3xl font-bold">{averageScore}</p>
            </div>

            <div className="rounded-2xl border border-gray-200 p-6">
              <p className="text-sm text-gray-500">Highest Opportunity</p>
              <p className="mt-2 text-3xl font-bold">{highestOpportunityCountry}</p>
            </div>
          </div>
        </div>
      </section>
    </main>
  );
}
