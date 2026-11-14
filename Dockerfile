FROM node:24-bookworm-slim
WORKDIR /app
COPY package.json package-lock.json ./
# Keeps dev packages: npm start runs the server through tsx.
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build && mkdir -p .cache/live-thesis && chown -R node:node .cache
ENV HOST=0.0.0.0 PORT=5174
USER node
EXPOSE 5174
CMD ["node", "--import", "tsx", "server/src/main.ts"]
