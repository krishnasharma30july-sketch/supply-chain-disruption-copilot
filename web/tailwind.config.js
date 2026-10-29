/** @type {import('tailwindcss').Config} */
// Visual identity: "logistics control room". Pine green for structure, signal amber for attention, warm neutrals.
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["'IBM Plex Sans'", "ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "Roboto", "sans-serif"],
        mono: ["'IBM Plex Mono'", "ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
      },
      colors: {
        brand: {
          50: "#eef6f3", 100: "#d5e9e2", 200: "#acd3c5", 300: "#7bb6a3", 400: "#4f957f", 500: "#337a65",
          600: "#256452", 700: "#1f5143", 800: "#1b4237", 900: "#16362e", 950: "#0b1f1a",
        },
        signal: {
          50: "#fff8eb", 100: "#feecc7", 200: "#fdd78a", 300: "#fcbc4d", 400: "#fba424", 500: "#f5820b",
          600: "#d95f06", 700: "#b44109", 800: "#92330e", 900: "#782b0f",
        },
        // Warm neutral in place of the default cool slate.
        slate: {
          50: "#f7f6f3", 100: "#efede8", 200: "#e2dfd7", 300: "#cdc8bc", 400: "#a39d8f", 500: "#7a7467",
          600: "#5d584d", 700: "#47433b", 800: "#2f2c27", 900: "#1e1c19", 950: "#12110f",
        },
      },
      borderRadius: { md: "0.3125rem", lg: "0.4375rem", xl: "0.5625rem" },
      boxShadow: {
        sm: "0 1px 0 rgba(30, 28, 25, 0.04), 0 1px 2px rgba(30, 28, 25, 0.06)",
        panel: "0 1px 0 rgba(30, 28, 25, 0.05), 0 8px 24px -12px rgba(22, 54, 46, 0.18)",
      },
      letterSpacing: { label: "0.08em" },
    },
  },
  plugins: [],
};
