import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// /api goes to the FastAPI server, so the browser only ever talks to one address.
// Locally that is `make serve`; in Docker, API_URL points at the server container.
const api = process.env.API_URL ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { port: 5173, proxy: { "/api": api } },
  preview: { port: 5173, proxy: { "/api": api } },
});
