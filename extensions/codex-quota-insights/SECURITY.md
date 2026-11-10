# Security Policy

## Supported versions

Only the latest release is supported.

## Reporting a vulnerability

Please open a minimal security report without credentials or private account data. Do not include `auth.json`, access tokens, API keys, session logs, or raw App Server responses containing account information.

## Trust boundary

Codex Quota Insights starts the locally installed `codex app-server` process over stdio. It does not read Codex credential files or send quota data to a third-party service. The optional dashboard binds only to `127.0.0.1` and does not support non-loopback hosts.

Users are responsible for installing Codex from a trusted source and protecting their local Codex account and operating-system session.
