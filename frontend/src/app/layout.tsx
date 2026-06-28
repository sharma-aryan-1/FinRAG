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

// metadataBase makes relative OG/Twitter URLs absolute and is what lets the link
// unfurl into a proper preview card when shared (LinkedIn, Slack, iMessage).
const SITE_URL = 'https://finrag-front.vercel.app';
const TITLE = 'FinRAG: Agentic RAG over SEC 10-K filings';
const DESCRIPTION =
  'An agentic RAG system over SEC 10-K filings: hybrid retrieval, cross-encoder reranking, ' +
  'DuckDB structured-data fusion, and a streamed LangGraph agent. Built by Aryan Sharma.';

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
  title: TITLE,
  description: DESCRIPTION,
  authors: [{ name: 'Aryan Sharma', url: 'https://www.linkedin.com/in/sharmaaryan25/' }],
  creator: 'Aryan Sharma',
  openGraph: {
    type: 'website',
    url: SITE_URL,
    siteName: 'FinRAG',
    title: TITLE,
    description: DESCRIPTION,
  },
  twitter: {
    card: 'summary',
    title: TITLE,
    description: DESCRIPTION,
  },
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
