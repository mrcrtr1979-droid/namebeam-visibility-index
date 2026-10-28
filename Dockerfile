# Container for the MCP server in mcp/ (receipts-index-mcp), used by directories that run
# the server to check it (for example Glama). Builds from source and runs over stdio.
FROM node:22-alpine
WORKDIR /app
COPY mcp/ ./
RUN npm ci && npm run build && npm prune --omit=dev
# Directory health checks are not agent usage: telemetry is off inside this image.
ENV DO_NOT_TRACK=1
ENTRYPOINT ["node", "dist/src/index.js"]
