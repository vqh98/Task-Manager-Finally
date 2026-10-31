# TaskAdviser on Telegram Serverless

This directory is the migration target for the existing Python/Aiogram bot.
Telegram Serverless executes JavaScript modules in its own V8 runtime, so the
Python application cannot be uploaded as-is.

## Safety boundary

The production bot remains on the VPS until feature parity and data migration
have both been verified. Deploying endpoint modules is safe, but synchronizing
the webhook is intentionally excluded from every script in this directory.

Never commit the CLI access token. Provide it only at runtime:

```bash
export TGCLOUD_TOKEN='app...:...'
npm ci
npx tgcloud status
```

## Migration order

1. Port the SQLite schema to `tgcloud/schema.js` and write an idempotent importer.
2. Port message and callback-query routing from Aiogram to JavaScript handlers.
3. Replace APScheduler jobs with supported Serverless scheduling primitives, or
   keep a minimal scheduler on the VPS if the platform cannot provide parity.
4. Port outbound OpenAI and Google Calendar calls through `sdk/fetch`.
5. Import a production database snapshot and run parity tests without switching
   the webhook.
6. Pause the VPS bot briefly, import the final delta, deploy handlers, then run
   `tgcloud webhook sync` once. Keep a tested rollback path to the VPS webhook.

The initial `endpoints/health.js` module is only a deployment/connectivity probe.
