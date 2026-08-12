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
RUN npm run build

FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY nginx-ratelimit.conf /etc/nginx/conf.d/00-ratelimit.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
