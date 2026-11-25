FROM node:22-alpine AS builder

WORKDIR /app

ARG VITE_API_BASE_URL=http://localhost:8001
ARG VITE_SUPABASE_URL=https://placeholder.supabase.co
ARG VITE_SUPABASE_ANON_KEY=placeholder
ARG VITE_DEV_AUTH_ENABLED=false

ENV VITE_API_BASE_URL=$VITE_API_BASE_URL \
    VITE_SUPABASE_URL=$VITE_SUPABASE_URL \
    VITE_SUPABASE_ANON_KEY=$VITE_SUPABASE_ANON_KEY \
    VITE_DEV_AUTH_ENABLED=$VITE_DEV_AUTH_ENABLED

COPY package.json package-lock.json* ./
RUN npm ci || npm install

COPY . .
RUN npm run build

FROM nginx:1.27-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
