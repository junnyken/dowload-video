FROM node:22-alpine AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY nginx-ratelimit.conf /etc/nginx/conf.d/00-ratelimit.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
