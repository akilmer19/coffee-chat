#!/usr/bin/env python3
"""Random coffee-chat pairer for Slack.

Fetches the members of a designated channel, randomly pairs them (a trio when
the count is odd), opens a group DM per pairing, and posts an intro prompting a
15-minute chat. Optionally avoids repeating the previous round's pairings.
"""
import datetime
import json
import os
import random
from itertools import combinations
from pathlib import Path

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
CHANNEL_ID = os.environ["SLACK_CHANNEL_ID"]
HISTORY_PATH = Path(os.environ.get("HISTORY_PATH", "state/history.json"))
AVOID_REPEATS = os.environ.get("AVOID_REPEATS", "true").lower() == "true"
HISTORY_WINDOW = int(os.environ.get("HISTORY_WINDOW", "5"))  # rounds to avoid repeats across
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"
# Biweekly cadence. The workflow fires every Tuesday; we only pair on ISO weeks
# whose number matches BIWEEKLY_PARITY. Parity 1 (odd weeks) => Nov 3, 17, Dec 1...
BIWEEKLY = os.environ.get("BIWEEKLY", "true").lower() == "true"
BIWEEKLY_PARITY = int(os.environ.get("BIWEEKLY_PARITY", "1"))
# Hybrid notifications: post a kickoff to the channel in addition to the DMs.
CHANNEL_POST = os.environ.get("CHANNEL_POST", "true").lower() == "true"

# One is picked at random per pairing. Written to read fine for a pair or a trio.
# Slack mrkdwn: *bold*, :emoji: shortcodes.
INTROS = [
    ":coffee: *Coffee's on!* You've been matched for a 15-minute chat this week. "
    "No agenda, no slides — just good company. Pick a time and enjoy! :speech_balloon:",

    ":wave: *Surprise — you've been paired!* Grab 15 minutes and a drink :coffee:, "
    "and get to know each other. Reply here to lock in a slot. Enjoy!",

    ":sparkles: *A wild coffee chat appeared!* Your name's on 15 minutes this week. "
    "Say hi, swap stories, enjoy. :coffee:",

    ":handshake: *New match unlocked!* Spend 15 minutes together this week — no prep "
    "required. Reply here to find a time. :tada:",

    ":coffee::speech_balloon: *Your weekly coffee crew is here!* Set up a quick 15-min "
    "hang and trade stories. Enjoy the chat!",

    ":alarm_clock: *15 minutes, zero agenda.* You've been randomly matched for a coffee "
    "chat this week — go say hi and enjoy! :coffee:",

    ":tada: *Matched!* The coffee gods have spoken. Find 15 minutes this week to "
    "connect — enjoy every sip. :coffee:",

    ":seedling: *Fresh pairing, just for you.* Grab 15 minutes this week to get to know "
    "your match. No prep, just vibes. Enjoy! :coffee:",

    ":mag: *Look who got matched!* A 15-minute coffee chat is headed your way this week. "
    "Reply here to pick a time and enjoy! :speech_balloon:",

    ":rocket: *Pairing incoming!* 15 minutes, your match, and a coffee of your choice "
    ":coffee:. Enjoy the chat this week!",
]

# Posted once to the whole channel before DMs go out (the "hybrid" kickoff).
# Keeps who-is-matched-with-whom private while signaling the round is live.
CHANNEL_POSTS = [
    ":coffee: *Coffee chats are brewing!* This round's matches just went out — "
    "check your DMs to meet your pair. Enjoy! :speech_balloon:",

    ":wave: *It's coffee chat day!* New pairings are in your DMs right now. "
    "Find 15 minutes this round and say hi! :coffee:",

    ":sparkles: *Fresh matches are out!* Peek at your DMs to see who you're grabbing "
    "a coffee with this round. Have fun! :coffee:",

    ":tada: *A new round of coffee chats has begun!* Your match is waiting in your DMs — "
    "go set up a quick 15-minute hang. Enjoy!",

    ":mega: *Coffee chats are live!* Everyone's just been matched — check your DMs for "
    "your partner this round and enjoy the chat! :coffee:",
]


def get_members(client):
    """Return human (non-bot, active) member IDs of the channel.

    Also stashes a diagnostic snapshot (raw count, who was filtered out and why)
    on the function for the caller to record. No message content, just IDs.
    """
    ids, cursor = [], None
    while True:
        resp = client.conversations_members(channel=CHANNEL_ID, cursor=cursor, limit=200)
        ids.extend(resp["members"])
        cursor = resp.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break

    humans, skipped = [], []
    for uid in ids:
        if uid == "USLACKBOT":
            skipped.append({"id": uid, "why": "slackbot"})
            continue
        info = client.users_info(user=uid)["user"]
        if info.get("is_bot"):
            skipped.append({"id": uid, "why": "bot", "name": info.get("name")})
            continue
        if info.get("deleted"):
            skipped.append({"id": uid, "why": "deleted", "name": info.get("name")})
            continue
        humans.append(uid)

    get_members.diagnostic = {
        "channel_id": CHANNEL_ID,
        "raw_member_count": len(ids),
        "eligible_count": len(humans),
        "skipped": skipped,
    }
    return humans


def write_diagnostic(extra):
    """Write state/last_run.json so a run's outcome is inspectable from the repo."""
    diag = dict(getattr(get_members, "diagnostic", {}))
    diag.update(extra)
    diag["ran_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    (HISTORY_PATH.parent / "last_run.json").write_text(json.dumps(diag, indent=2))


def make_groups(users):
    """Split users into pairs; one trio if the count is odd."""
    u = users[:]
    random.shuffle(u)
    if len(u) < 2:
        return []
    groups = []
    if len(u) % 2 == 1:
        groups.append([u.pop(), u.pop(), u.pop()])  # carve off one trio
    while u:
        groups.append([u.pop(), u.pop()])
    return groups


def recent_pairs(history):
    """Every pair seen across the stored rounds — the repeat-avoidance window."""
    pairs = set()
    for rnd in history.get("rounds", []):
        for group in rnd:
            for a, b in combinations(sorted(group), 2):
                pairs.add((a, b))
    return pairs


def repeat_count(groups, recent):
    return sum(
        1
        for group in groups
        for a, b in combinations(sorted(group), 2)
        if (a, b) in recent
    )


def best_groups(users, recent, tries=200):
    """Best-of-N shuffle: minimize pairs that repeat last round."""
    best, best_score = None, None
    for _ in range(tries):
        g = make_groups(users)
        score = repeat_count(g, recent)
        if best_score is None or score < best_score:
            best, best_score = g, score
            if score == 0:
                break
    return best


def announce(client, groups):
    # Hybrid kickoff: one public post to the channel, then private DMs per match.
    if CHANNEL_POST and not DRY_RUN:
        client.chat_postMessage(channel=CHANNEL_ID, text=random.choice(CHANNEL_POSTS))

    for group in groups:
        if DRY_RUN:
            print("Would pair:", group)
            continue
        channel = client.conversations_open(users=",".join(group))["channel"]["id"]
        client.chat_postMessage(channel=channel, text=random.choice(INTROS))


def save_history(groups, history):
    """Append this round and keep only the most recent HISTORY_WINDOW rounds."""
    rounds = history.get("rounds", [])
    rounds.append(groups)
    rounds = rounds[-HISTORY_WINDOW:]
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_PATH.write_text(json.dumps({"rounds": rounds}, indent=2))


def main():
    # The biweekly gate applies only to the scheduled cron. Manual runs
    # (workflow_dispatch) and local runs always pair, so testing isn't blocked.
    scheduled = os.environ.get("EVENT_NAME", "schedule") == "schedule"
    if BIWEEKLY and scheduled and not DRY_RUN:
        week = datetime.date.today().isocalendar()[1]
        if week % 2 != BIWEEKLY_PARITY:
            print(f"ISO week {week} is an off week (biweekly); skipping this run.")
            return

    client = WebClient(token=BOT_TOKEN)
    users = get_members(client)
    if len(users) < 2:
        print(f"Only {len(users)} eligible member(s); nothing to pair.")
        write_diagnostic({"group_sizes": [], "note": "fewer than 2 eligible members"})
        return

    history = {}
    if AVOID_REPEATS and HISTORY_PATH.exists():
        history = json.loads(HISTORY_PATH.read_text())
    recent = recent_pairs(history) if AVOID_REPEATS else set()

    groups = best_groups(users, recent)
    announce(client, groups)

    if AVOID_REPEATS and not DRY_RUN:
        save_history(groups, history)
    write_diagnostic({"group_sizes": sorted(len(g) for g in groups)})

    print(f"Paired {len(users)} people into {len(groups)} group(s).")


if __name__ == "__main__":
    try:
        main()
    except SlackApiError as e:
        raise SystemExit(f"Slack API error: {e.response['error']}")
