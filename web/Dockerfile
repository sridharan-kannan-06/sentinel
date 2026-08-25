FROM node:22-slim AS builder

WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1

COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund --ignore-scripts

COPY . ./
# Turbopack cannot resolve Carbon's internal relative Sass imports, so the build
# script pins webpack. See next.config.ts.
RUN npm run build

FROM node:22-slim

WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1

# The standalone output carries the server and only the dependencies it actually
# reached, which keeps this image a fraction of the size of node_modules.
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public

ENV PORT=8080
CMD ["node", "server.js"]
