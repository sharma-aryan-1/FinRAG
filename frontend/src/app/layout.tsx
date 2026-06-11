import type { Metadata } from 'next';
import { Inter, Fraunces, JetBrains_Mono } from 'next/font/google';
import './globals.css';

// Three-font system (ary.sh-inspired):
//  • Inter      — body / answers (legible)
//  • Fraunces   — serif display for big headings + the brand (elegant, high-contrast)
//  • JetBrainsMono — all labels/badges/meta (uppercase, letter-spaced — the techy signature)
const inter = Inter({ subsets: ['latin'], variable: '--font-inter', display: 'swap' });
const fraunces = Fraunces({ subsets: ['latin'], variable: '--font-serif', display: 'swap' });
const mono = JetBrains_Mono({ subsets: ['latin'], variable: '--font-mono', display: 'swap' });

export const metadata: Metadata = {
  title: 'FinRAG',
  description: 'Agentic RAG over SEC 10-K filings',
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className="h-full">
      <body
        className={`${inter.variable} ${fraunces.variable} ${mono.variable} font-sans h-full m-0 antialiased`}
      >
        {children}
      </body>
    </html>
  );
}
