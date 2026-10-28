#!/usr/bin/env python3
"""Random coffee-chat pairer for Slack.

Fetches the members of a designated channel, randomly pairs them (a trio when
the count is odd), opens a group DM per pairing, and posts an intro prompting a
15-minute chat. Optionally avoids repeating the previous round's pairings.
"""
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

INTRO = (
    ":coffee: *Coffee chat!* You've been randomly matched for a 15-minute hang "
    "this week.\n\n"
    "No agenda — grab a coffee (virtual or real) and get to know each other. "
    "Reply here to lock in a time that works for everyone. :speech_balloon:"
)


def get_members(client):
    """Return human (non-bot, active) member IDs of the channel."""
    ids, cursor = [], None
    while True:
        resp = client.conversations_members(channel=CHANNEL_ID, cursor=cursor, limit=200)
        ids.extend(resp["members"])
        cursor = resp.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break

    humans = []
    for uid in ids:
        if uid == "USLACKBOT":
            continue
        info = client.users_info(user=uid)["user"]
        if info.get("is_bot") or info.get("deleted"):
            continue
        humans.append(uid)
    return humans


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
    for group in groups:
        if DRY_RUN:
            print("Would pair:", group)
            continue
        channel = client.conversations_open(users=",".join(group))["channel"]["id"]
        client.chat_postMessage(channel=channel, text=INTRO)


def save_history(groups, history):
    """Append this round and keep only the most recent HISTORY_WINDOW rounds."""
    rounds = history.get("rounds", [])
    rounds.append(groups)
    rounds = rounds[-HISTORY_WINDOW:]
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_PATH.write_text(json.dumps({"rounds": rounds}, indent=2))


def main():
    client = WebClient(token=BOT_TOKEN)
    users = get_members(client)
    if len(users) < 2:
        print(f"Only {len(users)} eligible member(s); nothing to pair.")
        return

    history = {}
    if AVOID_REPEATS and HISTORY_PATH.exists():
        history = json.loads(HISTORY_PATH.read_text())
    recent = recent_pairs(history) if AVOID_REPEATS else set()

    groups = best_groups(users, recent)
    announce(client, groups)

    if AVOID_REPEATS and not DRY_RUN:
        save_history(groups, history)

    print(f"Paired {len(users)} people into {len(groups)} group(s).")


if __name__ == "__main__":
    try:
        main()
    except SlackApiError as e:
        raise SystemExit(f"Slack API error: {e.response['error']}")
