# Cloudflare Options Scanner

This Worker replaces the GitHub Actions workflows for the daily and 15-minute Upstox scans.

## Requirements

- Cloudflare Workers Paid plan. The current options CSV has 147 instruments; with today's data enabled, one scan can make about 294 Upstox subrequests. Workers Free allows 50 subrequests per invocation and will not run the full scan.
- Node.js and npm installed locally.
- A valid Upstox access token, Telegram bot token, and Telegram chat ID.

## Configure and deploy

From the repository root, install the Worker dependencies and authenticate:

```sh
npm install
npx wrangler login
```

The existing local Python scanners continue to use `requirements.txt`. Their Python dependencies are not bundled into the Cloudflare Worker.

Set the Cloudflare Worker secrets. Do not put their values in `wrangler.jsonc` or commit them:

```sh
npx wrangler secret put UPSTOX_TOKEN
npx wrangler secret put BOT_TOKEN
npx wrangler secret put CHAT_ID
npx wrangler secret put MANUAL_RUN_TOKEN
```

Deploy the Worker and its Cron Triggers:

```sh
npx wrangler deploy
```

The daily scan runs at 3:00 PM IST, Monday through Friday. The 15-minute scan runs every 15 minutes from 9:28 AM through 3:13 PM IST. Cloudflare Cron Triggers use UTC. The npm scripts copy only `config_options_daily.yaml`, `config_options_15min.yaml`, and `indian_options.csv` into `.cloudflare-assets` before starting or deploying the Worker; the local token and other repository files are not included as assets.

## Run manually

The Worker accepts authenticated POST requests at `/run`:

```sh
curl -X POST "https://akanip-options-scanner.<your-subdomain>.workers.dev/run" \
  -H "Authorization: Bearer <MANUAL_RUN_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"scan":"15min"}'
```

Use `{"scan":"daily"}` to run the daily scan. The endpoint returns 404 for requests without the `/run` path and rejects requests without the manual-run secret.

Run `npm run dry-run` before deployment to check the bundle. For local development, use `npm run dev`; Cron Trigger testing is described in Cloudflare's Worker Cron Trigger documentation.