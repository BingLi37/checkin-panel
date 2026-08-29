import {heroui} from "@heroui/theme";

/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
    "./node_modules/@heroui/theme/dist/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      // None of these four faces carries CJK glyphs, so the Chinese fallbacks have to be
      // spelled out — bare `sans-serif` picks something worse on Windows.
      fontFamily: {
        sans: ["'iA Writer Quattro S'", "'IBM Plex Sans'", "ui-sans-serif", "system-ui", "'PingFang SC'", "'Microsoft YaHei'", "'Noto Sans CJK SC'", "sans-serif"],
        mono: ["'Lilex'", "ui-monospace", "SFMono-Regular", "Consolas", "monospace"],
        serif: ["'IBM Plex Serif'", "'Songti SC'", "ui-serif", "Georgia", "serif"],
      },
      // The promo sticker's 反光: a light band sweeps across, then waits out the rest of the cycle,
      // so it reads as an occasional glint rather than a spinner. The band is 45% of the sticker
      // wide and translateX is relative to the band, hence -110% / 240% to clear both edges.
      // `prefers-reduced-motion` turns it off in index.css.
      keyframes: {
        shine: {
          '0%, 60%': { transform: 'translateX(-110%)' },
          '100%': { transform: 'translateX(240%)' },
        },
      },
      animation: {
        shine: 'shine 3.4s ease-in-out infinite',
      },
    },
  },
  darkMode: "class",
  plugins: [heroui()],
}
