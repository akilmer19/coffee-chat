# Coffee Chat for Slack

A tiny Donut-style bot: each week it randomly pairs the members of a Slack
channel, opens a group DM per pairing, and nudges them to grab a 15-minute
coffee chat. Odd headcount gets one trio. It avoids re-matching anyone you've
been paired with in the last 5 rounds. Runs free on GitHub Actions — no server
to maintain.

## Setup (~15 min)

### 1. Create the Slack app
1. Go to https://api.slack.com/apps → **Create New App** → **From scratch**.
2. Name it (e.g. "Coffee Chat"), pick your workspace.
3. **OAuth & Permissions** → add these **Bot Token Scopes**:
   - `channels:read` (use `groups:read` instead if the channel is private)
   - `users:read`
   - `mpim:write`
   - `chat:write`
4. **Install to Workspace**, then copy the **Bot User OAuth Token** (`xoxb-…`).

### 2. Pick the channel
1. Create or choose a channel, e.g. `#coffee-chat`, and have people join it —
   this is your opt-in pool.
2. **Invite the bot** to it: `/invite @Coffee Chat`.
3. Grab the channel ID: open the channel → click its name → bottom of the
   **About** tab shows an ID like `C0123ABCD`.

### 3. Drop these files in a repo and add secrets
1. Put this folder's contents in a GitHub repo (e.g. under `aksapience`).
2. Repo → **Settings → Secrets and variables → Actions → New repository secret**:
   - `SLACK_BOT_TOKEN` → the `xoxb-…` token
   - `SLACK_CHANNEL_ID` → the `C…` channel ID

### 4. Test it
- Repo → **Actions → Coffee Chat Pairings → Run workflow** to trigger a run by
  hand. Pairings should land in Slack within a few seconds.
- To preview without messaging anyone, run locally first:
  ```bash
  pip install -r requirements.txt
  SLACK_BOT_TOKEN=xoxb-... SLACK_CHANNEL_ID=C... DRY_RUN=true python pair.py
  ```

That's it — it now runs every Monday automatically.

## Knobs

| What | How |
|------|-----|
| **Schedule** | Edit the `cron` in `.github/workflows/coffee-chat.yml`. Current: Tuesdays 18:00 UTC (10am PT / 1pm ET in US standard time). GitHub cron is UTC and ignores DST, so in summer it lands an hour later. |
| **Cadence** | Biweekly by default (`BIWEEKLY=true`), pairing only on odd ISO weeks (`BIWEEKLY_PARITY=1`) → Nov 3, 17, Dec 1… Set `BIWEEKLY=false` for weekly, or flip `BIWEEKLY_PARITY` to `0` to run the opposite weeks. |
| **Message text** | Edit the `INTROS` list in `pair.py` — one is picked at random per pairing. Add/remove freely. |
| **Channel kickoff post** | On by default (`CHANNEL_POST=true`): posts one public message to the channel before the DMs, rotating through `CHANNEL_POSTS`. Set `CHANNEL_POST=false` for DMs only. |
| **Repeat-avoidance window** | Set env `HISTORY_WINDOW` (default `5` rounds). |
| **Allow repeat matches** | Set env `AVOID_REPEATS=false`. |
| **Trio vs. pairs** | Automatic — odd headcount produces exactly one trio. |

## How it works

- `pair.py` — fetches channel members (skips bots + deactivated), shuffles into
  pairs/one trio, opens an MPIM per group, posts the intro. Best-of-200 shuffle
  minimizes matches that repeat last round.
- `state/history.json` — the last 5 rounds' groups (user IDs only, no messages),
  committed back by the Action so repeat-avoidance persists across runs.
- `.github/workflows/coffee-chat.yml` — the weekly schedule + manual trigger.

## Buy vs. build

If zero maintenance matters more than control, **Donut**'s free tier covers small
teams and needs no code. Build this if you want to own the logic, the message,
and the data — or fold it into a broader internal bot later.
