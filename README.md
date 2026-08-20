# Hermes Agent on Railway

Deploy [Hermes Agent](https://hermes-agent.nousresearch.com/) to Railway with one click. Hermes is an open-source AI agent by Nous Research with tool use, memory, messaging platform integrations, and a web dashboard.

[![Deploy on Railway](https://railway.com/button.svg)](https://railway.com/template/TEMPLATE_ID?referralCode=REFERRAL_CODE)

## Features

This template goes beyond a basic Hermes deploy:

- **Full dashboard access** — manage config, API keys, sessions, logs, analytics, cron jobs, and skills from your browser. No SSH or CLI needed.
- **Messaging gateway included** — Telegram, Discord, and Slack bots run alongside the dashboard. Configure platform tokens in the UI, hit restart, and your bot is live.
- **Marketing review pipeline** — optionally generate one daily marketing source into Threads, Blog, and Card News drafts, then send it to a Discord review channel.
- **Gateway management widget** — a floating status indicator and restart button injected into the dashboard. See at a glance if the gateway is running, restart it after config changes without redeploying.
- **Cookie-based auth** — password-protected login page with session cookies. No repeated browser auth prompts like basic auth templates.
- **Auto-updates** — pulls the latest Hermes release on every container restart. Always up to date, no manual intervention. Disable with `AUTO_UPDATE=false` to pin a version.
- **Zero config to start** — deploy with just a password, then set up everything else (LLM provider, API keys, messaging platforms) from the dashboard UI.
- **Persistent storage** — attach a Railway volume to keep sessions, memories, config, and logs across redeploys.

## Setup

1. Click the **Deploy on Railway** button above
2. Set `DASHBOARD_PASSWORD` (required)
3. Deploy — log in at your Railway URL
4. Add your LLM provider key (e.g. OpenRouter) on the **API Keys** page
5. Optionally configure Telegram/Discord/Slack tokens and hit **Restart** on the gateway widget
6. For the marketing review pipeline, set the environment variables below and test with `python /marketing_pipeline.py --once`

## Environment Variables

| Variable | Description |
|---|---|
| `DASHBOARD_USER` | Login username (default: `admin`) |
| `DASHBOARD_PASSWORD` | Login password (**required** — deploy will fail without it) |
| `AUTO_UPDATE` | Pull latest Hermes on every restart (default: `true`, set to `false` to pin version) |
| `TZ` | Runtime timezone. Use `Asia/Seoul` for Korea scheduling. |
| `OPENROUTER_API_KEY` | OpenRouter key used by the marketing review pipeline. |
| `OPENROUTER_MODEL` | OpenRouter model, for example `anthropic/claude-3.5-sonnet`. |
| `DISCORD_WEBHOOK_REVIEW` | Preferred Discord webhook for `#03-검수대기`. |
| `DISCORD_WEBHOOK_검수대기` | Korean alias for the review webhook. |
| `DISCORD_WEBHOOK_승인대기` | Existing webhook alias used by this Railway project. |
| `DISCORD_BOT_TOKEN` | Optional fallback Discord bot token. |
| `DISCORD_CHANNEL_ID` | Optional fallback Discord channel ID for bot sending. |
| `MARKETING_SCHEDULER_ENABLED` | Set `true` to run the daily marketing pipeline. Keep `false` until manual tests pass. |
| `MARKETING_RUN_TIME` | Daily run time in `HH:MM`, interpreted using `TZ`. |
| `MARKETING_SOURCE_TOPIC` | Fallback source topic for manual and scheduled tests. |
| `STORE_NAME` | Store name included in the generation prompt. |
| `GDRIVE_SOURCE_SUBPATH` | Folder below `GDRIVE_ROOT_FOLDER_ID` to sync (default: `Obsidian`; use an empty value when the root ID is already the Obsidian folder). |
| `MARKETING_REQUIRE_OBSIDIAN` | Fail safely instead of generating an ungrounded draft when no Markdown sources are available (default: `true`). |

All other Hermes configuration is still available through the dashboard after deploy.

## Marketing Pipeline Tests

Run these inside the Railway shell or container after setting the required environment variables:

```bash
python /marketing_pipeline.py --test-llm
python /marketing_pipeline.py --test-discord
python /marketing_pipeline.py --test-gdrive
python /marketing_pipeline.py --once
```

Expected logs are stage-specific:

```text
[Job] started
[Hermes] job started
[LLM] request started
[LLM] success
[Discord] send started
[Discord] success
[Job] completed
```

Recommended rollout order:

1. Keep `MARKETING_SCHEDULER_ENABLED=false`.
2. Run `python /marketing_pipeline.py --test-llm`.
3. Run `python /marketing_pipeline.py --test-discord`.
4. Run `python /marketing_pipeline.py --once`.
5. Set `MARKETING_SCHEDULER_ENABLED=true` only after the manual end-to-end test reaches Discord.

## Persistent Storage

To keep your data across redeploys, attach a Railway volume:

1. Right-click the service in your Railway project
2. Select **Attach Volume**
3. Set mount path to `/root/.hermes`

This persists sessions, memories, API keys, config, logs, and cron jobs.

## Architecture

```text
Internet -> Railway -> Auth Proxy (cookie login) -> Hermes Dashboard (port 9119)
                           |
                           +-> Messaging Gateway (Telegram/Discord/Slack)
                           +-> Marketing Pipeline Scheduler -> OpenRouter -> Discord #03-review
                           +-> /api/health (unauthenticated, for Railway health checks)
                           +-> /api/gateway/restart (authenticated, restart bot)
                           +-> /api/gateway/status (authenticated, check bot status)
```

## Resources

- [Hermes Agent Documentation](https://hermes-agent.nousresearch.com/docs)
- [GitHub Repository](https://github.com/NousResearch/hermes-agent)
- [Web Dashboard Guide](https://hermes-agent.nousresearch.com/docs/user-guide/features/web-dashboard)
