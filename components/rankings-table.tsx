"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

type CountryCard = {
  code: string;
  name: string;
  region: string;
  capital: string;
  population: number;
  gdp: number;
  summary: string;
  score?: {
    overall?: number;
    risk?: number;
    opportunity?: number;
    power_score?: number | null;
    power_classification?: string | null;
  };
};

export default function RankingsTable({
  countries,
}: {
  countries: CountryCard[];
}) {
  const [selectedRegion, setSelectedRegion] = useState("All");
  const [sortBy, setSortBy] = useState("overall");

  const regions = useMemo(() => {
    const uniqueRegions = Array.from(new Set(countries.map((c) => c.region)));
    return ["All", ...uniqueRegions];
  }, [countries]);

  const filteredAndSorted = useMemo(() => {
    const filtered =
      selectedRegion === "All"
        ? countries
        : countries.filter((c) => c.region === selectedRegion);

    return [...filtered].sort((a, b) => {
      const aValue =
        sortBy === "risk"
          ? a.score?.risk ?? 0
          : sortBy === "opportunity"
            ? a.score?.opportunity ?? 0
            : sortBy === "power"
              ? Number(a.score?.power_score ?? 0)
              : a.score?.overall ?? 0;

      const bValue =
        sortBy === "risk"
          ? b.score?.risk ?? 0
          : sortBy === "opportunity"
            ? b.score?.opportunity ?? 0
            : sortBy === "power"
              ? Number(b.score?.power_score ?? 0)
              : b.score?.overall ?? 0;

      if (sortBy === "risk") {
        return aValue - bValue;
      }

      return bValue - aValue;
    });
  }, [countries, selectedRegion, sortBy]);

  return (
    <main className="min-h-screen bg-white px-6 py-10 text-gray-900">
      <div className="mx-auto max-w-5xl">
        <h1 className="text-4xl font-bold tracking-tight">Rankings</h1>
        <p className="mt-3 text-lg text-gray-600">
          Compare countries by overall structural performance, risk, opportunity, or composite
          power.
        </p>

        <div className="mt-8 grid gap-4 md:grid-cols-2">
          <select
            value={selectedRegion}
            onChange={(e) => setSelectedRegion(e.target.value)}
            className="w-full rounded-xl border border-gray-300 px-4 py-3 outline-none focus:border-black"
          >
            {regions.map((region) => (
              <option key={region} value={region}>
                {region}
              </option>
            ))}
          </select>

          <select
            value={sortBy}
            onChange={(e) => setSortBy(e.target.value)}
            className="w-full rounded-xl border border-gray-300 px-4 py-3 outline-none focus:border-black"
          >
            <option value="overall">Sort by Overall</option>
            <option value="risk">Sort by Risk (lower is better)</option>
            <option value="opportunity">Sort by Opportunity</option>
            <option value="power">Sort by Power</option>
          </select>
        </div>

        <div className="mt-4 text-sm text-gray-500">
          {filteredAndSorted.length} result
          {filteredAndSorted.length !== 1 ? "s" : ""}
        </div>

        <div className="mt-10 overflow-hidden rounded-2xl border border-gray-200">
          <table className="min-w-full text-left">
            <thead className="bg-gray-50 text-sm text-gray-600">
              <tr>
                <th className="px-6 py-4">Rank</th>
                <th className="px-6 py-4">Country</th>
                <th className="px-6 py-4">Region</th>
                <th className="px-6 py-4">Overall</th>
                <th className="px-6 py-4">Power</th>
                <th className="px-6 py-4">Risk</th>
                <th className="px-6 py-4">Opportunity</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200">
              {filteredAndSorted.map((country, index) => (
                <tr key={country.code} className="text-sm">
                  <td className="px-6 py-4 font-medium">{index + 1}</td>
                  <td className="px-6 py-4">
                    <Link
                      href={`/countries/${country.code}`}
                      className="font-medium hover:underline"
                    >
                      {country.name}
                    </Link>
                  </td>
                  <td className="px-6 py-4 text-gray-600">{country.region}</td>
                  <td className="px-6 py-4">{country.score?.overall ?? "-"}</td>
                  <td className="px-6 py-4">
                    <span className="font-medium">
                      {country.score?.power_score != null
                        ? country.score.power_score
                        : "-"}
                    </span>
                    {country.score?.power_classification ? (
                      <span className="mt-0.5 block text-xs text-gray-500">
                        {country.score.power_classification}
                      </span>
                    ) : null}
                  </td>
                  <td className="px-6 py-4">{country.score?.risk ?? "-"}</td>
                  <td className="px-6 py-4">
                    {country.score?.opportunity ?? "-"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </main>
  );
}
