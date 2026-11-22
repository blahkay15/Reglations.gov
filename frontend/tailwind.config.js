/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#1b2430",
        paper: "#f4f1ea",
        card: "#fffcf7",
        line: "#e4ddd0",
        pine: "#0e5c4c",
        pineDark: "#0a3f35",
        copper: "#8d4e2b",
        warn: "#8a4b08",
      },
      fontFamily: {
        sans: ["Segoe UI", "Helvetica Neue", "Arial", "sans-serif"],
        serif: ["Iowan Old Style", "Palatino Linotype", "Palatino", "Georgia", "serif"],
      },
    },
  },
  plugins: [],
};
