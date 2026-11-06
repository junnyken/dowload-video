FROM node:22-alpine AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
# frontend/.env is .dockerignore'd (never bake secrets into image layers via COPY) —
# Supabase creds are public-safe (anon key), so they come in as build args instead.
ARG VITE_SUPABASE_URL
ARG VITE_SUPABASE_ANON_KEY
ARG VITE_API_URL=""
ENV VITE_SUPABASE_URL=$VITE_SUPABASE_URL
ENV VITE_SUPABASE_ANON_KEY=$VITE_SUPABASE_ANON_KEY
ENV VITE_API_URL=$VITE_API_URL
# Public origin for canonical/OG/sitemap (read by scripts/prerender-seo.mjs).
# Empty -> script default https://dvid.vibe1.tinhgon.xyz
ARG VITE_SITE_URL=""
ENV VITE_SITE_URL=$VITE_SITE_URL
# Cloudflare Turnstile SITE key (public). Empty -> no captcha widget, no token
# sent. The SECRET key never comes here: it lives only in the Supabase
# dashboard (Auth -> Attack Protection). See docs/runbooks/antispam-signup.md.
ARG VITE_TURNSTILE_SITE_KEY=""
ENV VITE_TURNSTILE_SITE_KEY=$VITE_TURNSTILE_SITE_KEY
ARG VITE_GOOGLE_SITE_VERIFICATION=""
ENV VITE_GOOGLE_SITE_VERIFICATION=$VITE_GOOGLE_SITE_VERIFICATION
ARG VITE_BING_SITE_VERIFICATION=""
ENV VITE_BING_SITE_VERIFICATION=$VITE_BING_SITE_VERIFICATION
ARG VITE_INDEXNOW_KEY="7b9e4f2a8c1d6e3b5a0f9c8d7e6f5a4b"
ENV VITE_INDEXNOW_KEY=$VITE_INDEXNOW_KEY
RUN npm run build
# Files must be world-readable for nginx (a 600 checkout gave 403 on /icons/*.svg).
RUN chmod -R a+rX /app/dist

FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY nginx-ratelimit.conf /etc/nginx/conf.d/00-ratelimit.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
