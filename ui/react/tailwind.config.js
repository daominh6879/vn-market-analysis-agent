/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        bg: '#212121',
        sidebar: '#171717',
        surface: '#2f2f2f',
        border: '#3d3d3d',
        muted: '#888888',
        accent: '#10a37f',
        'accent-hover': '#0e8f6e',
        text: '#ececec',
        'text-muted': '#c5c5d2',
      },
      fontFamily: {
        sans: [
          'Söhne',
          'ui-sans-serif',
          'system-ui',
          '-apple-system',
          'sans-serif',
        ],
      },
    },
  },
  plugins: [],
}
