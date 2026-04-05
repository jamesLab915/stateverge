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
  };
};

export default function CountriesSearch({
  countries,
}: {
  countries: CountryCard[];
}) {
  const [query, setQuery] = useState("");
  const [selectedRegion, setSelectedRegion] = useState("All");

  const regions = useMemo(() => {
    const uniqueRegions = Array.from(new Set(countries.map((c) => c.region)));
    return ["All", ...uniqueRegions];
  }, [countries]);

  const filteredCountries = useMemo(() => {
    const q = query.trim().toLowerCase();

    return countries.filter((country) => {
      const matchesQuery =
        !q ||
        country.name.toLowerCase().includes(q) ||
        country.region.toLowerCase().includes(q) ||
        country.capital.toLowerCase().includes(q);

      const matchesRegion =
        selectedRegion === "All" || country.region === selectedRegion;

      return matchesQuery && matchesRegion;
    });
  }, [countries, query, selectedRegion]);

  return (
    <main className="min-h-screen bg-white px-6 py-10 text-gray-900">
      <div className="mx-auto max-w-6xl">
        <h1 className="text-4xl font-bold tracking-tight">Countries</h1>
        <p className="mt-3 text-lg text-gray-600">
          Structured country profiles for governance, economy, risk, opportunity, and future potential.
        </p>

        <div className="mt-8 grid gap-4 md:grid-cols-2">
          <input
            type="text"
            placeholder="Search by country, region, or capital..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="w-full rounded-xl border border-gray-300 px-4 py-3 outline-none focus:border-black"
          />

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
        </div>

        <div className="mt-4 text-sm text-gray-500">
          {filteredCountries.length} result{filteredCountries.length !== 1 ? "s" : ""}
        </div>

        <div className="mt-10 grid gap-6 md:grid-cols-2 xl:grid-cols-3">
          {filteredCountries.map((country) => (
            <div key={country.code} className="rounded-2xl border border-gray-200 p-6 shadow-sm">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <Link href={`/countries/${country.code}`}>
                    <h2 className="cursor-pointer text-2xl font-semibold hover:underline">
                      {country.name}
                    </h2>
                  </Link>
                  <p className="mt-1 text-sm text-gray-500">{country.region}</p>
                </div>

                <div className="rounded-full bg-gray-100 px-3 py-1 text-sm font-medium">
                  {country.score?.overall ?? "-"}
                </div>
              </div>

              <p className="mt-4 text-sm leading-6 text-gray-700">
                {country.summary}
              </p>

              <div className="mt-5 space-y-2 text-sm text-gray-600">
                <p>
                  <span className="font-medium text-gray-900">Capital:</span>{" "}
                  {country.capital}
                </p>
                <p>
                  <span className="font-medium text-gray-900">Population:</span>{" "}
                  {country.population.toLocaleString()}
                </p>
                <p>
                  <span className="font-medium text-gray-900">GDP:</span> $
                  {country.gdp.toLocaleString()}
                </p>
              </div>

              <div className="mt-5 flex gap-3 text-sm">
                <span className="rounded-full bg-gray-100 px-3 py-1">
                  Risk {country.score?.risk ?? "-"}
                </span>
                <span className="rounded-full bg-gray-100 px-3 py-1">
                  Opportunity {country.score?.opportunity ?? "-"}
                </span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </main>
  );
}
