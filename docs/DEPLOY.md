# Deploying Nightwarden

Target: a free **Hugging Face Space** (Docker) for the app, a free **Neon** Postgres database, and free
UptimeRobot + healthchecks.io to watch the watcher.

## 1. Database (Neon)

1. In Neon, create a project named `nightwarden` (separate from Citebase's).
2. Copy the connection string and change the scheme to `postgresql+psycopg2://…`, keeping `?sslmode=require`.

## 2. Space

1. huggingface.co → New Space → name `nightwarden`, SDK **Docker**, blank template, hardware **CPU basic (free)**.
2. Push this repo to the Space: `git remote add space https://huggingface.co/spaces/<user>/nightwarden` then
   `git push space main` (use a Hugging Face access token with write rights as the password). The YAML header at
   the top of `README.md` tells the Space to build the Dockerfile and serve port 7860.

3. Settings → Variables and secrets. Add as **Secrets**:

   | Name | Value |
   |---|---|
   | `DATABASE_URL` | the Neon string from step 1 |
   | `TARGET_METRICS_TOKEN` | the same `METRICS_TOKEN` set on Citebase's Render service |
   | `GROQ_API_KEY` | a Groq key (a separate one from Citebase's is better) |
   | `ADMIN_TOKEN` | a long random string |
   | `NOTIFY_WEBHOOK_URL` | Discord channel → Integrations → Webhooks → copy URL |
   | `BREVO_API_KEY` | optional, for page emails |
   | `HEARTBEAT_URL` | from step 3 |

   And as **Variables**: `TARGET_URL=https://documind-intel.onrender.com`, `MAIL_FROM`, `ALERT_EMAIL`,
   `PUBLIC_URL=https://<user>-nightwarden.hf.space`.

4. The Space builds and runs `alembic upgrade head`, then the server. Open it: the Live tab should show
   "last poll … ago" within a minute.

## 3. Watching the watcher

- **healthchecks.io**: new check, period 5 min, grace 5 min. Its ping URL is `HEARTBEAT_URL`. If polling stops,
  it emails you.
- **UptimeRobot**: HTTP monitor on `https://<user>-nightwarden.hf.space/health`, every 5 min. This also keeps the
  free Space from sleeping.

## 4. Check alerts end to end

```bash
curl -X POST https://<user>-nightwarden.hf.space/api/events \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H "Content-Type: application/json" \
  -d '{"kind":"error_spike","detail":"manual test","metrics":{"error_rate":0.5,"requests_5m":40}}'
```

A Discord message should arrive, and an incident should appear on the dashboard. Resolve it with
`POST /api/incidents/<id>/resolve`.

## 5. Agent eval

GitHub → repo Settings → Secrets → Actions → add `GROQ_API_KEY`. Then Actions → **Agent eval** → Run workflow.
Download `eval-results`, commit it as `eval/results.json`, and the Evaluation tab shows the agent column.

## Security notes

- Never commit `.env`. All secrets live in the Space settings.
- `TARGET_METRICS_TOKEN` only reads metrics and redacted warning logs; rotate it on both sides if exposed.
- Without `ADMIN_TOKEN`, the write endpoints return 503.
