import { getCountryCardsFromDB } from "@/lib/db/queries";

export default async function TestDbPage() {
  const countries = await getCountryCardsFromDB();

  return (
    <main className="min-h-screen bg-white px-6 py-10 text-gray-900">
      <div className="mx-auto max-w-5xl">
        <h1 className="text-3xl font-bold">Database Test</h1>

        <div className="mt-8 space-y-4">
          {countries.map((country) => (
            <div key={country.code} className="rounded-xl border p-4">
              <p className="text-xl font-semibold">{country.name}</p>
              <p className="text-sm text-gray-500">{country.region}</p>
              <p className="mt-2 text-sm">
                Overall: {country.score?.overall} | Risk: {country.score?.risk} | Opportunity: {country.score?.opportunity}
              </p>
            </div>
          ))}
        </div>
      </div>
    </main>
  );
}
