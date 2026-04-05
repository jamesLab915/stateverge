import "./globals.css";
import Link from "next/link";

export const metadata = {
  title: "StateVerge",
  description: "Country intelligence system",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className="bg-white text-gray-900">
        <header className="border-b border-gray-200 px-6 py-4">
          <div className="mx-auto flex max-w-6xl items-center justify-between">
            <Link href="/" className="text-xl font-bold">
              StateVerge
            </Link>

            <nav className="flex flex-wrap gap-6 text-sm text-gray-600">
              <Link href="/countries" className="hover:text-black">
                Countries
              </Link>
              <Link href="/power" className="hover:text-black">
                Power
              </Link>
              <Link href="/rankings" className="hover:text-black">
                Rankings
              </Link>
              <Link href="/compare/us-cn" className="hover:text-black">
                Compare
              </Link>
              <Link href="/timeline/us" className="hover:text-black">
                Timeline
              </Link>
              <Link href="/scenario/us" className="hover:text-black">
                Scenarios
              </Link>
              <Link href="/causal/us" className="hover:text-black">
                Causal
              </Link>
              <Link href="/us/power" className="hover:text-black">
                US Federal
              </Link>
            </nav>
          </div>
        </header>

        {children}
      </body>
    </html>
  );
}
