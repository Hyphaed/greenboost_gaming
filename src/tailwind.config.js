
/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        nvidia: "#76B900",
        background: "#000000",
        surface: "#1A1A1A",
        "surface-light": "#2A2A2A",
        accent: "#2D2D2D",
        divider: "#333333",
      },
    },
  },
  plugins: [],
}
