# ---------- Stage 1 : build ----------
FROM node:22-alpine AS builder

WORKDIR /app

# Copie manifests d'abord pour cacher `npm ci`
COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# API URL bakée à build (Next.js NEXT_PUBLIC_* est inline dans le bundle)
ARG NEXT_PUBLIC_API_URL=/api
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL

RUN npm run build

# ---------- Stage 2 : runtime minimal ----------
FROM node:22-alpine AS runner

WORKDIR /app

ENV NODE_ENV=production \
    PORT=3000 \
    HOSTNAME=0.0.0.0

# Utilisateur non-root
RUN addgroup -g 1001 nodejs && adduser -S nextjs -u 1001

# Copie du build standalone (contient node_modules minimum)
COPY --from=builder --chown=nextjs:nodejs /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs

EXPOSE 3000

CMD ["node", "server.js"]
