#!/usr/bin/env node

import http from "node:http";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { AppServerClient, buildStatus } from "./codex-quota.mjs";

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const indexPath = path.resolve(scriptDirectory, "../web/index.html");
const staticAssets = new Map([
  [
    "/styles.css",
    {
      file: path.resolve(scriptDirectory, "../web/styles.css"),
      contentType: "text/css; charset=utf-8",
    },
  ],
  [
    "/app.js",
    {
      file: path.resolve(scriptDirectory, "../web/app.js"),
      contentType: "text/javascript; charset=utf-8",
    },
  ],
]);
const host = "127.0.0.1";
const port = parsePort(process.env.CODEX_QUOTA_DASHBOARD_PORT || "8788");
const codexBin = process.env.CODEX_BIN || "codex";
let cachedHtml;

function parsePort(value) {
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed < 1 || parsed > 65535) {
    throw new Error(
      "CODEX_QUOTA_DASHBOARD_PORT must be an integer between 1 and 65535",
    );
  }
  return parsed;
}

function sendJson(response, statusCode, payload) {
  response.writeHead(statusCode, {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
  });
  response.end(`${JSON.stringify(payload)}\n`);
}

async function readQuota() {
  const client = new AppServerClient({ codexBin, timeoutMs: 15_000 });
  try {
    await client.start();
    return buildStatus(await client.request("account/rateLimits/read"));
  } finally {
    client.close();
  }
}

const server = http.createServer(async (request, response) => {
  const url = new URL(request.url || "/", `http://${host}:${port}`);
  if (request.method !== "GET") {
    response.writeHead(405, { Allow: "GET" });
    return response.end();
  }

  if (url.pathname === "/api/quota") {
    try {
      const quota = await readQuota();
      return sendJson(response, 200, {
        schemaVersion: "codex-quota-dashboard/v1",
        quota,
        fetchedAtIso: new Date().toISOString(),
      });
    } catch (error) {
      return sendJson(response, 503, {
        error: "quota_unavailable",
        message: error instanceof Error ? error.message : String(error),
      });
    }
  }

  const asset = staticAssets.get(url.pathname);
  if (asset) {
    try {
      const body = await readFile(asset.file);
      response.writeHead(200, {
        "Content-Type": asset.contentType,
        "Cache-Control": "no-cache",
        "X-Content-Type-Options": "nosniff",
      });
      return response.end(body);
    } catch (error) {
      response.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
      return response.end(
        `Missing asset: ${error instanceof Error ? error.message : String(error)}\n`,
      );
    }
  }

  if (url.pathname === "/" || url.pathname === "/index.html") {
    cachedHtml ||= await readFile(indexPath);
    response.writeHead(200, {
      "Content-Type": "text/html; charset=utf-8",
      "Cache-Control": "no-cache",
      "Content-Security-Policy":
        "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
      "Referrer-Policy": "no-referrer",
      "X-Content-Type-Options": "nosniff",
      "X-Frame-Options": "DENY",
    });
    return response.end(cachedHtml);
  }

  response.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
  response.end("Not found\n");
});

server.listen(port, host, () => {
  process.stdout.write(`Codex Quota Dashboard: http://${host}:${port}/\n`);
  process.stdout.write("Local-only server. Press Ctrl+C to stop.\n");
});

server.on("error", (error) => {
  process.stderr.write(`dashboard-server: ${error.message}\n`);
  process.exitCode = 1;
});
