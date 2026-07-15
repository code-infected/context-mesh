/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        // Chrome tokens are CSS variables so light/dark swap in one place (src/index.css).
        page: "var(--cm-page)",
        surface: "var(--cm-surface)",
        ink: "var(--cm-ink)",
        "ink-2": "var(--cm-ink-2)",
        "ink-3": "var(--cm-ink-3)",
        grid: "var(--cm-grid)",
        axis: "var(--cm-axis)",
        hairline: "var(--cm-hairline)",
        accent: "var(--cm-accent)",
        "accent-soft": "var(--cm-accent-soft)",
      },
      fontFamily: {
        sans: ["system-ui", "-apple-system", "Segoe UI", "sans-serif"],
      },
    },
  },
  plugins: [],
};
