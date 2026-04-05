import Link from "next/link";

export default function USFederalLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="border-t border-gray-100 bg-slate-50/50">
      <div className="mx-auto max-w-6xl px-6 py-3">
        <p className="text-xs font-semibold uppercase tracking-widest text-slate-500">
          US Federal · Power &amp; causality
        </p>
        <nav className="mt-2 flex flex-wrap gap-4 text-sm text-slate-700">
          <Link href="/us/power" className="hover:text-black">
            Power overview
          </Link>
          <Link href="/us/actors" className="hover:text-black">
            Actors
          </Link>
          <Link href="/us/events" className="hover:text-black">
            Events
          </Link>
          <Link href="/us/causal" className="hover:text-black">
            Causal
          </Link>
          <Link href="/us/timeline" className="hover:text-black">
            Timeline
          </Link>
          <Link href="/us/scenario" className="hover:text-black">
            Scenarios
          </Link>
          <Link
            href="/us/compare/us-president/us-vice-president"
            className="hover:text-black"
          >
            Compare
          </Link>
          <Link href="/countries/us" className="hover:text-black">
            US country profile
          </Link>
        </nav>
      </div>
      {children}
    </div>
  );
}
