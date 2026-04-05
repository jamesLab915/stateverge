"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

type Country = {
  code: string;
  name: string;
};

export default function CompareSelector({
  countries,
  defaultLeft,
  defaultRight,
}: {
  countries: Country[];
  defaultLeft: string;
  defaultRight: string;
}) {
  const router = useRouter();
  const [left, setLeft] = useState(defaultLeft);
  const [right, setRight] = useState(defaultRight);

  const handleCompare = () => {
    if (!left || !right || left === right) return;
    router.push(`/compare/${left}-${right}`);
  };

  return (
    <div className="mt-8 rounded-2xl border border-gray-200 p-6">
      <h2 className="text-xl font-semibold">Compare Countries</h2>

      <div className="mt-4 grid gap-4 md:grid-cols-3">
        <select
          value={left}
          onChange={(e) => setLeft(e.target.value)}
          className="rounded-xl border border-gray-300 px-4 py-3 outline-none focus:border-black"
        >
          {countries.map((country) => (
            <option key={country.code} value={country.code}>
              {country.name}
            </option>
          ))}
        </select>

        <select
          value={right}
          onChange={(e) => setRight(e.target.value)}
          className="rounded-xl border border-gray-300 px-4 py-3 outline-none focus:border-black"
        >
          {countries.map((country) => (
            <option key={country.code} value={country.code}>
              {country.name}
            </option>
          ))}
        </select>

        <button
          onClick={handleCompare}
          className="rounded-xl bg-black px-5 py-3 text-white disabled:opacity-50"
          disabled={!left || !right || left === right}
        >
          Compare Now
        </button>
      </div>

      {left === right && (
        <p className="mt-3 text-sm text-red-500">
          Please choose two different countries.
        </p>
      )}
    </div>
  );
}
