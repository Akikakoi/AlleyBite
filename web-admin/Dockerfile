# 管理后台镜像（文档 9.5）：与 C 端同仓不同入口、独立部署。
# 多阶段构建：Vite 产物由独立 nginx 托管，并反代 /api 到内网 api 服务。
FROM node:20-alpine AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
# 生产构建读取 .env.production（VITE_API_BASE=/api，经本容器 nginx 反代）
RUN npm run build

FROM nginx:1.27-alpine

RUN rm -f /etc/nginx/conf.d/default.conf
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80 8443

CMD ["nginx", "-g", "daemon off;"]