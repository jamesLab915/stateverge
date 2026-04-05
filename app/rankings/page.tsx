export const dynamic = "force-dynamic";

import { getRankingsFromDB } from "@/lib/db/queries";
import RankingsTable from "@/components/rankings-table";

export default async function RankingsPage() {
  const countries = await getRankingsFromDB();
  return <RankingsTable countries={countries} />;
}
