#!/usr/bin/env python3
"""Fetch @liam.codez Instagram stats, grade the week/month, render dashboard.html.

Run daily. Appends a snapshot to data/history.json (one per calendar day,
re-running on the same day overwrites that day's snapshot), computes weekly and
monthly report-card grades, and injects everything into templates/dashboard.html
to produce dashboard.html at the repo root.
"""

import json
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

USERNAME = "liam.codez"
REPO = Path(__file__).resolve().parent.parent
HISTORY_PATH = REPO / "data" / "history.json"
TEMPLATE_PATH = REPO / "templates" / "dashboard.html"
OUTPUT_PATH = REPO / "dashboard.html"

PROFILE_URL = (
    "https://www.instagram.com/api/v1/users/web_profile_info/?username=" + USERNAME
)
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "x-ig-app-id": "936619743392459",
}


def fetch_profile(retries: int = 5) -> dict | None:
    """Fetch the live profile. Pass a saved profile JSON path as argv[1] to skip
    the network. Returns None when Instagram can't be reached (rate limit etc.).

    Instagram rate-limits by IP, so a burst of quick retries rarely helps —
    back off generously (5, 15, 30, 60, 90s) to ride out a short 429 window."""
    if len(sys.argv) > 1:
        return json.loads(Path(sys.argv[1]).read_text())["data"]["user"]
    backoffs = [5, 15, 30, 60, 90]
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(PROFILE_URL, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp)["data"]["user"]
        except Exception as err:  # noqa: BLE001 - retry any transport error
            last_err = err
            if attempt < retries - 1:
                time.sleep(backoffs[min(attempt, len(backoffs) - 1)])
    print(f"warning: could not fetch Instagram profile ({last_err}); "
          "falling back to the latest saved snapshot", file=sys.stderr)
    return None


def parse_posts(user: dict) -> list[dict]:
    posts = []
    for edge in user.get("edge_owner_to_timeline_media", {}).get("edges", []):
        node = edge["node"]
        caption_edges = node.get("edge_media_to_caption", {}).get("edges", [])
        caption = caption_edges[0]["node"]["text"] if caption_edges else ""
        posts.append(
            {
                "id": node["id"],
                "shortcode": node.get("shortcode", ""),
                "ts": node["taken_at_timestamp"],
                "date": datetime.fromtimestamp(
                    node["taken_at_timestamp"], tz=timezone.utc
                ).strftime("%Y-%m-%d"),
                "is_video": node.get("is_video", False),
                "likes": node.get("edge_liked_by", {}).get("count", 0),
                "comments": node.get("edge_media_to_comment", {}).get("count", 0),
                "views": node.get("video_view_count") or 0,
                "caption": caption,
            }
        )
    posts.sort(key=lambda p: p["ts"], reverse=True)
    return posts


def load_history() -> list[dict]:
    if HISTORY_PATH.exists():
        return json.loads(HISTORY_PATH.read_text())
    return []


def save_snapshot(history: list[dict], user: dict, posts: list[dict]) -> list[dict]:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    snap = {
        "date": today,
        "followers": user["edge_followed_by"]["count"],
        "following": user["edge_follow"]["count"],
        "total_posts": user.get("edge_owner_to_timeline_media", {}).get("count", 0),
        "posts": posts,
    }
    history = [h for h in history if h["date"] != today]
    history.append(snap)
    history.sort(key=lambda h: h["date"])
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_PATH.write_text(json.dumps(history, indent=1))
    return history


def interp(x: float, points: list[tuple[float, float]]) -> float:
    """Piecewise-linear interpolation through sorted (x, score) points."""
    if x <= points[0][0]:
        return points[0][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return points[-1][1]


# Posting cadence → score. Calibrated for a creator posting quality content,
# where a steady ~1.5-2 posts/week (6-8 a month) is a healthy rhythm, not a D.
CADENCE_WEEK = [(0, 0), (1, 55), (2, 72), (3, 85), (4, 95), (5, 100)]
CADENCE_MONTH = [(0, 0), (2, 40), (4, 58), (6, 70), (8, 82), (12, 95), (16, 100)]
# Engagement rate per post ((likes+comments)/followers) → score.
ENGAGEMENT = [(0, 10), (0.001, 30), (0.004, 55), (0.008, 70), (0.015, 85), (0.03, 100)]
# Follower growth over the window, as a fraction → score.
GROWTH_WEEK = [(-0.01, 20), (0, 50), (0.002, 60), (0.005, 70), (0.01, 85), (0.02, 100)]
GROWTH_MONTH = [(-0.02, 20), (0, 50), (0.01, 60), (0.02, 70), (0.04, 85), (0.08, 100)]

LETTERS = [
    (95, "A+"), (90, "A"), (85, "A-"), (80, "B+"), (75, "B"), (70, "B-"),
    (65, "C+"), (60, "C"), (55, "C-"), (50, "D"), (0, "F"),
]


def letter(score: float) -> str:
    for cutoff, grade in LETTERS:
        if score >= cutoff:
            return grade
    return "F"


def growth_over(history: list[dict], days: int) -> tuple[float | None, int | None]:
    """Follower growth vs the oldest snapshot within the window (fraction, absolute)."""
    if len(history) < 2:
        return None, None
    latest = history[-1]
    cutoff = (
        datetime.strptime(latest["date"], "%Y-%m-%d") - timedelta(days=days)
    ).strftime("%Y-%m-%d")
    candidates = [h for h in history[:-1] if h["date"] >= cutoff]
    if not candidates:
        return None, None
    base = candidates[0]
    if not base["followers"]:
        return None, None
    delta = latest["followers"] - base["followers"]
    return delta / base["followers"], delta


def grade_window(posts, followers, days, cadence_curve, growth_curve, history,
                 cadence_weight, engagement_weight, growth_weight, as_of):
    cutoff = as_of - timedelta(days=days)
    window = [p for p in posts if datetime.fromtimestamp(p["ts"], tz=timezone.utc) >= cutoff]

    likes = sum(p["likes"] for p in window)
    comments = sum(p["comments"] for p in window)
    views = sum(p["views"] for p in window)
    n = len(window)
    avg_eng = (likes + comments) / n if n else 0
    er = avg_eng / followers if followers else 0

    cadence_score = interp(n, cadence_curve)
    engagement_score = interp(er, ENGAGEMENT) if n else 0
    growth_frac, growth_abs = growth_over(history, days)

    parts = [
        ("Posting cadence", cadence_score, cadence_weight,
         f"{n} post{'s' if n != 1 else ''} in {days} days"),
        ("Engagement", engagement_score, engagement_weight,
         f"{er * 100:.2f}% of followers engage per post"),
    ]
    if growth_frac is not None:
        parts.append(("Follower growth", interp(growth_frac, growth_curve), growth_weight,
                      f"{growth_abs:+,} followers ({growth_frac * 100:+.2f}%)"))

    total_weight = sum(w for _, _, w, _ in parts)
    score = sum(s * w for _, s, w, _ in parts) / total_weight

    return {
        "days": days,
        "posts": n,
        "likes": likes,
        "comments": comments,
        "views": views,
        "avg_eng": round(avg_eng, 1),
        "er_pct": round(er * 100, 2),
        "growth_pct": None if growth_frac is None else round(growth_frac * 100, 2),
        "growth_abs": growth_abs,
        "score": round(score),
        "grade": letter(score),
        "components": [
            {"label": lbl, "score": round(s), "detail": d} for lbl, s, _, d in parts
        ],
    }


def display_caption(caption: str, limit: int = 90) -> str:
    first = caption.split("\n")[0]
    words = [w for w in first.split() if not w.startswith("#")]
    text = " ".join(words) or first
    return text[: limit - 1] + "…" if len(text) > limit else text


WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday",
            "Friday", "Saturday", "Sunday"]


def top_post(posts, as_of, days):
    """The standout post in the window, with a plain-language 'why'."""
    cutoff = as_of - timedelta(days=days)
    window = [p for p in posts
              if datetime.fromtimestamp(p["ts"], tz=timezone.utc) >= cutoff]
    if not window:
        return None
    eng = lambda p: p["likes"] + p["comments"]  # noqa: E731
    best = max(window, key=eng)
    avg = sum(eng(p) for p in window) / len(window)
    mult = eng(best) / avg if avg else 1
    if mult >= 1.2:
        why = f"{mult:.1f}× your average this period"
    elif mult <= 0.85:
        why = "a quieter one — below your usual"
    else:
        why = "right around your average"
    return {
        "date": best["date"],
        "caption": display_caption(best["caption"], 80),
        "likes": best["likes"],
        "comments": best["comments"],
        "eng": eng(best),
        "url": f"https://www.instagram.com/p/{best['shortcode']}/" if best["shortcode"] else "",
        "format": "Reel" if best.get("is_video") else "Post",
        "why": why,
        "window_days": days,
    }


def best_day(posts, as_of, days=90):
    """Average engagement per weekday across RECENT posts only (directional —
    the public API exposes ~12 posts, and old viral outliers would skew it, so
    we window to the last `days`)."""
    cutoff = as_of - timedelta(days=days)
    recent = [p for p in posts
              if datetime.fromtimestamp(p["ts"], tz=timezone.utc) >= cutoff]
    buckets: dict[int, list[int]] = {}
    for p in recent:
        wd = datetime.fromtimestamp(p["ts"], tz=timezone.utc).weekday()
        buckets.setdefault(wd, []).append(p["likes"] + p["comments"])
    if len(recent) < 4 or len(buckets) < 2:
        return None
    per = [{"day": WEEKDAYS[wd], "avg": round(sum(v) / len(v)), "n": len(v)}
           for wd, v in buckets.items()]
    best = max(per, key=lambda d: d["avg"])
    return {"best": best, "sample": len(recent)}


def milestone(followers, history):
    """Next round-number milestone and, if we have history, a pace-based ETA."""
    step = 500 if followers < 10000 else 1000 if followers < 100000 else 10000
    nxt = (followers // step + 1) * step
    per_day = eta_days = eta_date = None
    if len(history) >= 2:
        first, last = history[0], history[-1]
        span = (datetime.strptime(last["date"], "%Y-%m-%d")
                - datetime.strptime(first["date"], "%Y-%m-%d")).days
        if span > 0:
            per_day = (last["followers"] - first["followers"]) / span
            if per_day > 0.1:
                eta_days = round((nxt - followers) / per_day)
                eta_date = (datetime.strptime(last["date"], "%Y-%m-%d")
                            + timedelta(days=eta_days)).strftime("%Y-%m-%d")
    return {
        "current": followers,
        "next": nxt,
        "step": step,
        "remaining": nxt - followers,
        "per_day": None if per_day is None else round(per_day, 1),
        "eta_days": eta_days,
        "eta_date": eta_date,
    }


def main() -> None:
    now = datetime.now(timezone.utc)
    user = fetch_profile()
    history = load_history()
    fresh = user is not None
    if fresh:
        posts = parse_posts(user)
        history = save_snapshot(history, user, posts)
        followers = user["edge_followed_by"]["count"]
        as_of = now
    elif history:
        # Instagram was unreachable. Grade the last known-good snapshot relative
        # to WHEN it was captured, so the report reflects that day rather than
        # silently decaying to an F as days pass with no fresh data.
        latest = history[-1]
        posts = latest["posts"]
        followers = latest["followers"]
        as_of = datetime.strptime(latest["date"], "%Y-%m-%d").replace(
            hour=23, minute=59, second=59, tzinfo=timezone.utc)
        user = {
            "username": USERNAME,
            "edge_followed_by": {"count": followers},
            "edge_follow": {"count": latest["following"]},
            "edge_owner_to_timeline_media": {"count": latest["total_posts"]},
            "is_verified": True,
        }
    else:
        raise SystemExit("no live data and no saved history — nothing to render")

    week = grade_window(posts, followers, 7, CADENCE_WEEK, GROWTH_WEEK, history,
                        cadence_weight=0.35, engagement_weight=0.45, growth_weight=0.20,
                        as_of=as_of)
    month = grade_window(posts, followers, 30, CADENCE_MONTH, GROWTH_MONTH, history,
                         cadence_weight=0.30, engagement_weight=0.40, growth_weight=0.30,
                         as_of=as_of)

    data = {
        "generated_at": now.isoformat(),
        "data_as_of": as_of.strftime("%Y-%m-%d"),
        "stale": not fresh,
        "profile": {
            "username": user["username"],
            "full_name": user.get("full_name", ""),
            "followers": followers,
            "following": user["edge_follow"]["count"],
            "total_posts": user.get("edge_owner_to_timeline_media", {}).get("count", 0),
            "verified": user.get("is_verified", False),
            "bio": user.get("biography", ""),
        },
        "history": [
            {"date": h["date"], "followers": h["followers"], "total_posts": h["total_posts"]}
            for h in history
        ],
        "week": week,
        "month": month,
        "insight": {
            "top_post": top_post(posts, as_of, 7) or top_post(posts, as_of, 30),
            "best_day": best_day(posts, as_of),
        },
        "milestone": milestone(followers, history),
        "recent_posts": [
            {
                "date": p["date"],
                "caption": display_caption(p["caption"]),
                "likes": p["likes"],
                "comments": p["comments"],
                "views": p["views"],
                "url": f"https://www.instagram.com/p/{p['shortcode']}/" if p["shortcode"] else "",
                "in_week": (as_of - datetime.fromtimestamp(p["ts"], tz=timezone.utc)).days < 7,
                "in_month": (as_of - datetime.fromtimestamp(p["ts"], tz=timezone.utc)).days < 30,
            }
            for p in posts[:12]
        ],
    }

    template = TEMPLATE_PATH.read_text()
    payload = json.dumps(data).replace("</", "<\\/")  # never break out of the <script> tag
    OUTPUT_PATH.write_text(template.replace("__DASH_DATA__", payload))
    print(f"dashboard.html written — week {week['grade']} ({week['score']}), "
          f"month {month['grade']} ({month['score']}), {followers:,} followers"
          f"{'' if fresh else '  [STALE: Instagram unreachable, using ' + as_of.strftime('%Y-%m-%d') + ']'}")


if __name__ == "__main__":
    main()
