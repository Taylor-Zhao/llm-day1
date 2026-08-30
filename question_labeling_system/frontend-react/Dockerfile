# 构建阶段使用与本地验证一致的 Node 22。
FROM node:22-alpine AS build
# 设置 React 前端工作目录。
WORKDIR /app
# 先复制包清单以复用 Docker 依赖缓存。
COPY package.json package-lock.json ./
# 使用锁文件执行可复现安装。
RUN npm ci
# 复制页面入口与 TypeScript/Vite 配置。
COPY index.html vite.config.ts tsconfig.json tsconfig.app.json tsconfig.node.json ./
# 复制 React 源码和静态资源。
COPY src ./src
COPY public ./public
# 执行 TypeScript 检查和生产打包。
RUN npm run build

# 运行阶段只使用 Nginx 托管静态资源和反向代理 API。
FROM nginx:1.27-alpine AS runtime
# 删除 Nginx 默认站点。
RUN rm /etc/nginx/conf.d/default.conf
# 安装 React 工作台站点配置。
COPY nginx.conf /etc/nginx/conf.d/default.conf
# 从构建阶段复制静态产物。
COPY --from=build /app/dist /usr/share/nginx/html
# 声明非特权 HTTP 端口。
EXPOSE 8080
# 前台运行 Nginx 便于容器平台管理。
CMD ["nginx", "-g", "daemon off;"]
