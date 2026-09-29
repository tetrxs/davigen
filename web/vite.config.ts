import path from "node:path"
import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"

// The built UI goes into the Python package (davigen/ui), which davigen's local server serves as it is:
// nobody who installs davigen needs Node.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": path.resolve(__dirname, "./src") } },
  build: { outDir: "../davigen/ui", emptyOutDir: true, chunkSizeWarningLimit: 1500 },
  server: { proxy: { "/api": "http://127.0.0.1:8765", "/basic": "http://127.0.0.1:8765", "/catalog": "http://127.0.0.1:8765", "/project": "http://127.0.0.1:8765" } },
})
