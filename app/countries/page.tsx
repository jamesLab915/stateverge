import { getCountryCardsFromDB } from "@/lib/db/queries";
import CountriesSearch from "@/components/countries-search";

export default async function CountriesPage() {
  const countries = await getCountryCardsFromDB();
  return <CountriesSearch countries={countries} />;
}
