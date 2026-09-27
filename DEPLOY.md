# Deploying the Vera bot

The bot is a single Docker web service. It needs one public HTTPS URL that serves `/v1/*`.

## Render (recommended)

1. Push this folder to a Git repo (GitHub/GitLab) and in Render choose **New → Blueprint**, then select the repo. `render.yaml` sets everything up.
   Or choose **New → Web Service → Docker** and set the health check path to `/v1/healthz`.
2. In the service's **Environment** tab, set:
   - `LLM_API_KEY`: an Anthropic API key (or set `LLM_PROVIDER=openai` / `openai_compatible` with `LLM_BASE_URL`).
   - `TEAM_NAME`, `TEAM_MEMBERS` (comma-separated), `CONTACT_EMAIL`: shown on the leaderboard.
3. Deploy, then check:
   ```bash
   curl https://<service>.onrender.com/v1/healthz     # {"status":"ok",...}
   curl https://<service>.onrender.com/v1/metadata
   ```
4. Submit `https://<service>.onrender.com` (the base URL, without `/v1`) on the challenge page.

## Must-haves

- **Exactly one instance and one worker.** Contexts, suppression keys and conversations live in memory. Two instances would split them.
- **No sleeping.** Use a paid instance (Render Starter or above). Free instances spin down when idle, and the judge disqualifies a bot after 3 consecutive failed health checks.
- **Keep it running** until results arrive. A restart wipes the pushed contexts mid-test.
- Without `LLM_API_KEY` the bot still works in template-only mode, but messages score higher with the LLM.

- **Do not set `PLAYGROUND=1` on the deployed service.** The playground can wipe the bot's memory and is only meant for local use.

## Anywhere else

```bash
docker build -t vera-bot .
docker run -p 8080:8080 -e LLM_PROVIDER=anthropic -e LLM_API_KEY=... vera-bot
```
Any host works (Fly.io, Railway, a VM behind HTTPS) as long as the rules above hold.

## Optional local checks

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/pytest                                   # 180 tests, no network needed
BOT_URL=https://<service>.onrender.com JUDGE_PROVIDER=anthropic JUDGE_API_KEY=... scripts/run_judge.sh
```
