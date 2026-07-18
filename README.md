# Liam's Creator Report

A one-glance daily dashboard for **@liam.codez** (Instagram) and its inbox
(liamcodez03@gmail.com). Live page:

**https://claude.ai/code/artifact/be653aac-d90a-40ab-95c7-bb0d408ed4a1**

## What it shows

- **Weekly grade** (A+ … F) for the last 7 days on Instagram — posting cadence,
  likes/comments, engagement rate, and follower growth once history accumulates.
- **Monthly grade** for the last 30 days, plus a follower-growth sparkline built
  from daily snapshots.
- **Per-post stats** for the last 30 days of posts (likes, comments, plays where
  Instagram exposes them), with links to each post.
- **Overnight inbox** — read live from the Gmail connector every time the page is
  opened inside claude.ai. Important emails are ranked first; nothing is stored.
- **Brand deals** — emails matching deal language (collab, sponsorship,
  partnership, ambassador, …) over the last 30 days, with offers-this-month,
  closed-this-month and closed-all-time counters. "Closed" marks persist in the
  browser via localStorage.

## How it works

```
scripts/update_dashboard.py   fetch → snapshot → grade → render
templates/dashboard.html      the page (design + live Gmail logic)
data/history.json             one snapshot per day (followers, posts, stats)
dashboard.html                generated output, published as the artifact
```

`update_dashboard.py` pulls the public profile API for @liam.codez, appends a
daily snapshot to `data/history.json` (re-running the same day overwrites that
day), computes the grades, and injects everything into the template. If
Instagram rate-limits the fetch it falls back to the latest saved snapshot; a
saved profile JSON can also be passed as the first argument.

### Grading rubric

Scores are piecewise-linear (see the curves at the top of the script), weighted:

| Window | Cadence | Engagement rate | Follower growth |
|--------|---------|-----------------|-----------------|
| Week   | 35%     | 45%             | 20%             |
| Month  | 30%     | 40%             | 30%             |

Growth is skipped (weights renormalized) until at least two daily snapshots
exist. Letters: ≥95 A+, ≥90 A, ≥85 A-, ≥80 B+, ≥75 B, ≥70 B-, ≥65 C+, ≥60 C,
≥55 C-, ≥50 D, else F.

## Daily refresh

A Claude Code Routine runs every morning (~7:20 am Pacific): it re-runs the
script, commits the new snapshot to the `claude/social-email-dashboard-z1evre`
branch, and republishes the artifact at the same URL.

## Gmail

The page calls the viewer's own Gmail connector (`window.claude.mcp`) — connect
**Gmail** in claude.ai → Settings → Connectors, then reload the dashboard. Email
never touches this repo.
