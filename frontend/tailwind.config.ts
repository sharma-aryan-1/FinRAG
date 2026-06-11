import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        background: "var(--background)",
        foreground: "var(--foreground)",
        // Brand accent — lime #bef264 (== Tailwind lime-300). Exposed as a token
        // so it's tweakable in one place; the lime-* scale is still used directly
        // for hover/border/ring shades that need lighter/darker steps.
        accent: "#bef264",
      },
      fontFamily: {
        // Injected as CSS vars by next/font in layout. serif = display headings,
        // mono = labels/meta, sans = body.
        sans: ["var(--font-inter)", "ui-sans-serif", "system-ui", "sans-serif"],
        serif: ["var(--font-serif)", "Georgia", "ui-serif", "serif"],
        mono: ["var(--font-mono)", "ui-monospace", "SFMono-Regular", "monospace"],
      },
    },
  },
  plugins: [],
};
export default config;
