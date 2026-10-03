# The web app: built once, then served by vite preview, which also forwards /api to the server.
FROM node:20-slim
WORKDIR /app
RUN npm install -g pnpm@10
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY . .
RUN pnpm build
ENV API_URL=http://server:8000
EXPOSE 5173
CMD ["pnpm", "preview", "--host", "0.0.0.0", "--port", "5173", "--strictPort"]
