# Deploying Nightwarden

Free setup: **Vercel** (Hobby) runs the app, **Neon** stores its data, **cron-job.org** triggers a poll every
14 minutes, and **healthchecks.io** emails you if polling ever stops.

Why this shape: Vercel's free plan has no always-on process and its own cron runs only once a day, so an external
scheduler calls `/api/cron/poll`. Every 14 minutes keeps Citebase's free Render service awake (it sleeps after 15
idle minutes), and leaves both Neon databases idle often enough to stay inside the free 100 compute-hours a month
(Neon sleeps after 5 idle minutes).

## 1. Database (Neon)

1. console.neon.tech → **New Project** → name `nightwarden`, region **AWS US East 1 (N. Virginia)** (next to
   Vercel's default region) → Create.
2. **Connect** → copy the connection string (`postgresql://…?sslmode=require…`). Use it as is.

## 2. Vercel project

1. vercel.com → **Add New… → Project** → import `Vegess/nightwarden`. Framework preset: **FastAPI** (detected).
2. Before the first deploy, open **Environment Variables** and add these for **Production only** (the build
   runs database migrations, so previews must not point at the production database):

   | Name | Value |
   |---|---|
   | `DATABASE_URL` | the Neon string |
   | `TARGET_URL` | `https://documind-intel.onrender.com` |
   | `TARGET_METRICS_TOKEN` | the same `METRICS_TOKEN` as on Citebase's Render service |
   | `GROQ_API_KEY` | a Groq key (a separate one from Citebase's is better) |
   | `ADMIN_TOKEN` | a long random string |
   | `CRON_SECRET` | another long random string |
   | `DOWN_AFTER_POLLS` | `2` (two missed polls ≈ 28 min, then target_down) |
   | `POLL_TIMEOUT_S` | `20` (answer the scheduler within its 30 s limit even when Citebase is down) |
   | `NOTIFY_WEBHOOK_URL` | Discord channel → Edit → Integrations → Webhooks → copy URL |
   | `HEARTBEAT_URL` | from step 4 |
   | `PUBLIC_URL` | `https://<project>.vercel.app` |
   | `BREVO_API_KEY`, `MAIL_FROM`, `ALERT_EMAIL` | optional, for page emails |

3. Deploy. The build runs `python -m alembic upgrade head`. Open the URL: the dashboard says "Waiting for the
   first poll".

## 3. Scheduler (cron-job.org)

1. cron-job.org → sign up (free) → **Create cronjob**.
2. URL `https://<project>.vercel.app/api/cron/poll`, schedule **every 14 minutes** (custom: minutes
   `*/14`).
3. **Advanced** → Request method `POST`, add header `Authorization` = `Bearer <CRON_SECRET>`. cron-job.org
   waits 30 s. A normal poll takes a few seconds; one that triages an event can take longer and shows as a
   timeout there. That's fine: the poll is already saved, and if a triage is cut off it runs on the next
   poll. (cron-job.org disables a job only after many failures in a row.)
4. Turn on failure notifications.

## 4. Watching the watcher (healthchecks.io)

New check → period **15 minutes**, grace **20 minutes**. Its ping URL is `HEARTBEAT_URL`. Every successful poll
pings it; if polls stop, it emails you.

## 5. Check alerts end to end

```bash
curl -X POST https://<project>.vercel.app/api/events \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H "Content-Type: application/json" \
  -d '{"kind":"error_spike","detail":"manual test","metrics":{"error_rate":0.5,"requests_5m":40}}'
```

A Discord message arrives and an incident appears on the dashboard. Resolve it with
`POST /api/incidents/<id>/resolve`.

## Other hosts

The Dockerfile runs the same app with a background poller (`POLL_INTERVAL_S`, default 60 s) on any container
host. Mind the free-tier limits above if the database or the watched service sleeps.

## Security notes

- Never commit `.env`; secrets live in the host's settings.
- Without `ADMIN_TOKEN` the write endpoints return 503; without `CRON_SECRET` the poll endpoint does.
- `TARGET_METRICS_TOKEN` only reads metrics and redacted warning logs; rotate it on both sides if exposed.
