#!/usr/bin/env python3
"""Fetch high-citation linear algebra papers for the daily digest.

Source is the **Semantic Scholar** API rather than arXiv, deliberately.

arXiv gives you brand-new preprints: nothing is peer-reviewed at submission, and a
measured sample of 100 recent cs.LG papers had citation-bearing metadata on only
5% (journal_ref) / 3% (doi). There is essentially no quality signal on a paper
published yesterday — citations take 1-2 years to accumulate. So a daily feed of
fresh preprints is unvetted work by construction.

Semantic Scholar exposes what arXiv lacks: real citation counts and peer-reviewed
venue names. This module trades freshness for reliability — every paper here has
been read and cited by other researchers.

Because a high-citation corpus is stable, querying the same topics daily would
show the same papers forever. Two mechanisms keep the digest varied:
  1. Topic rotation — each day queries a different slice of topics (day-of-year).
  2. A seen-cache — papers already sent are skipped until the cache is rotated.
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

S2_SEARCH = "https://api.semanticscholar.org/graph/v1/paper/search"
S2_FIELDS = "title,year,citationCount,venue,authors,externalIds,abstract,openAccessPdf"

USER_AGENT = "daily-digest-bot/1.0 (+https://github.com/)"
TIMEOUT = 40

# Where to remember what we've already sent. Kept next to the script; in CI this
# is ephemeral unless the workflow caches it (see README).
SEEN_PATH = os.environ.get(
    "LINALG_SEEN_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".seen_papers.json"),
)
SEEN_LIMIT = 400  # ids to retain before dropping the oldest

# A reservoir of vetted papers, refilled opportunistically. This is what makes the
# digest survive a rate-limited morning: measured behaviour on the anonymous tier
# is that even one request after a 4-minute cooldown can 429, since the quota is
# shared across every unauthenticated client. Rather than fail, the bot serves
# from this pool and tops it up whenever the API does answer.
POOL_PATH = os.environ.get(
    "LINALG_POOL_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".paper_pool.json"),
)
POOL_TARGET = 30  # papers per bucket to keep banked

# Topic pool, grouped by bucket. Each entry is a Semantic Scholar search query.
# Rotating through these is what keeps a stable corpus from going stale.
TOPICS = {
    "numerical": [
        "randomized numerical linear algebra",
        "randomized SVD low-rank approximation",
        "preconditioning Krylov subspace methods",
        "tensor decomposition algorithms",
        "matrix sketching streaming algorithms",
        "sparse direct linear solvers",
        "eigenvalue algorithms large scale",
        "least squares regression numerical stability",
    ],
    "ml": [
        "low-rank adaptation large language models",
        "matrix factorization neural networks",
        "attention mechanism linear algebra efficiency",
        "model compression quantization low-rank",
        "spectral methods deep learning",
        "kernel methods random features",
    ],
    "graphics": [
        "geometry processing Laplacian mesh",
        "gaussian splatting radiance fields",
        "mesh parameterization surface",
        "skinning deformation linear blend",
        "differentiable rendering inverse graphics",
    ],
    "quantum": [
        "quantum linear algebra block encoding",
        "HHL quantum linear systems algorithm",
        "quantum singular value transformation",
        "variational quantum eigensolver",
        "quantum machine learning kernels",
    ],
    "theory": [
        "matrix analysis perturbation theory",
        "representation theory linear groups",
        "spectral graph theory eigenvalues",
        "random matrix theory applications",
    ],
}

# How many papers each bucket contributes per digest.
BUCKET_SLOTS = {
    "numerical": 2,
    "ml": 1,
    "graphics": 1,
    "quantum": 1,
    "theory": 1,
}

BUCKET_LABELS = {
    "numerical": "applied / numerical",
    "ml": "ML",
    "graphics": "graphics",
    "quantum": "quantum",
    "theory": "theory",
}

# A paper must clear this to count as "proven". Tuned per bucket: quantum LA and
# graphics are smaller fields that cite less in absolute terms than ML, so a flat
# threshold would silently starve them.
MIN_CITATIONS = {
    "numerical": 25,
    "ml": 40,
    "graphics": 20,
    "quantum": 15,
    "theory": 20,
}

# Only consider work from this year onward — old enough to be cited, recent
# enough to still be relevant. Surveys from the 1990s aren't useful daily reading.
YEAR_RANGE = os.environ.get("LINALG_YEAR_RANGE", "2015-2026")

# arXiv.org appears as a "venue" in Semantic Scholar for unpublished preprints.
# Since the whole point here is peer-reviewed work, those are filtered out.
NON_VENUES = {"arxiv.org", "arxiv", "", "ssrn", "biorxiv", "researchgate"}


# Semantic Scholar's anonymous tier is a shared bucket — roughly 1 request/sec
# across *all* unauthenticated clients worldwide, so a 429 usually means someone
# else spent the budget, not that we were greedy. Retries therefore need real
# backoff, and the gap between queries has to be generous. Set S2_API_KEY to get
# a private quota and make all of this mostly moot.
API_KEY = os.environ.get("S2_API_KEY", "").strip()
REQUEST_GAP = float(os.environ.get("LINALG_REQUEST_GAP", "1.0" if API_KEY else "3.5"))


def _get(url, retries=4):
    last_error = None
    for attempt in range(retries):
        try:
            headers = {"User-Agent": USER_AGENT}
            if API_KEY:
                headers["x-api-key"] = API_KEY
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            last_error = e
            if e.code in (429, 503) and attempt < retries - 1:
                # Exponential: 4s, 8s, 16s. Slow, but a morning digest can wait.
                time.sleep(4 * (2 ** attempt))
                continue
            raise
        except urllib.error.URLError as e:
            last_error = e
            if attempt < retries - 1:
                time.sleep(2)
                continue
            raise
    raise last_error


def load_seen():
    """Ids already sent. Missing or corrupt cache is not fatal — just start over."""
    try:
        with open(SEEN_PATH) as fh:
            data = json.load(fh)
            return list(data.get("ids", []))
    except (OSError, json.JSONDecodeError, AttributeError):
        return []


def save_seen(ids):
    trimmed = ids[-SEEN_LIMIT:]
    try:
        with open(SEEN_PATH, "w") as fh:
            json.dump({"ids": trimmed}, fh)
    except OSError as e:
        # A read-only filesystem shouldn't break the digest; we just repeat
        # papers next run.
        print(f"[s2] could not persist seen-cache: {e}", file=sys.stderr)


def load_pool():
    """Banked papers by bucket: {bucket: [paper, ...]}."""
    try:
        with open(POOL_PATH) as fh:
            data = json.load(fh)
        pool = data.get("buckets") or {}
        return {b: list(pool.get(b, [])) for b in TOPICS}
    except (OSError, json.JSONDecodeError, AttributeError):
        return {b: [] for b in TOPICS}


def save_pool(pool):
    trimmed = {b: papers[:POOL_TARGET * 2] for b, papers in pool.items()}
    try:
        with open(POOL_PATH, "w") as fh:
            json.dump({"buckets": trimmed}, fh)
    except OSError as e:
        print(f"[s2] could not persist pool: {e}", file=sys.stderr)


def todays_topics(day_index, per_bucket=1):
    """Pick which topics to query today, rotating by day so the digest varies."""
    chosen = {}
    for bucket, topics in TOPICS.items():
        if not topics:
            continue
        start = (day_index * per_bucket) % len(topics)
        picks = [topics[(start + i) % len(topics)] for i in range(min(per_bucket, len(topics)))]
        chosen[bucket] = picks
    return chosen


def _is_real_venue(venue):
    return (venue or "").strip().lower() not in NON_VENUES


def search_topic(query, limit=80):
    """One Semantic Scholar search. Returns [] on failure rather than raising."""
    params = {
        "query": query,
        "fields": S2_FIELDS,
        "limit": limit,
        "year": YEAR_RANGE,
    }
    url = f"{S2_SEARCH}?{urllib.parse.urlencode(params)}"
    try:
        raw = _get(url)
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        print(f"[s2] '{query}' failed: {e}", file=sys.stderr)
        return []
    try:
        return json.loads(raw).get("data") or []
    except json.JSONDecodeError as e:
        print(f"[s2] '{query}' bad json: {e}", file=sys.stderr)
        return []


def _paper_id(paper):
    ext = paper.get("externalIds") or {}
    return (
        ext.get("DOI")
        or ext.get("ArXiv")
        or paper.get("paperId")
        or (paper.get("title") or "").lower()[:80]
    )


def _clean(paper, bucket):
    ext = paper.get("externalIds") or {}
    arxiv_id = ext.get("ArXiv")
    doi = ext.get("DOI")

    # Prefer a link that resolves to something readable.
    if arxiv_id:
        url = f"https://arxiv.org/abs/{arxiv_id}"
    elif (paper.get("openAccessPdf") or {}).get("url"):
        url = paper["openAccessPdf"]["url"]
    elif doi:
        url = f"https://doi.org/{doi}"
    else:
        url = f"https://www.semanticscholar.org/paper/{paper.get('paperId', '')}"

    return {
        "id": _paper_id(paper),
        "title": " ".join((paper.get("title") or "(untitled)").split()),
        "year": paper.get("year"),
        "citations": paper.get("citationCount") or 0,
        "venue": " ".join((paper.get("venue") or "").split()),
        "authors": [a.get("name") for a in (paper.get("authors") or []) if a.get("name")],
        "url": url,
        "bucket": bucket,
    }


def refill_pool(pool, day_index, seen):
    """Top up thin buckets from the API. Best-effort: failures are expected and
    simply leave the pool as it was."""
    topics_today = todays_topics(day_index)
    fetched_any = False

    for bucket, queries in topics_today.items():
        if len(pool.get(bucket, [])) >= POOL_TARGET:
            continue  # already well stocked; don't spend a request

        floor = MIN_CITATIONS.get(bucket, 20)
        known = {p["id"] for p in pool.get(bucket, [])}

        for query in queries:
            results = search_topic(query)
            if results:
                fetched_any = True
            for raw in results:
                paper = _clean(raw, bucket)
                if paper["citations"] < floor:
                    continue
                if not _is_real_venue(paper["venue"]):
                    continue  # preprint with no peer-reviewed venue
                if paper["id"] in known or paper["id"] in seen:
                    continue
                known.add(paper["id"])
                pool.setdefault(bucket, []).append(paper)
            time.sleep(REQUEST_GAP)

        pool[bucket] = sorted(
            pool.get(bucket, []), key=lambda p: p["citations"], reverse=True
        )

    return fetched_any


def fetch_linalg_papers(day_index=0, slots=None, skip_seen=True):
    """Return proven, high-citation papers across buckets.

    Serves from a banked pool and refills it opportunistically, so a rate-limited
    morning still produces a digest. Returns [] only if the pool is empty *and*
    the API is unreachable.
    """
    slots = slots or BUCKET_SLOTS
    seen = set(load_seen()) if skip_seen else set()

    pool = load_pool()
    refill_pool(pool, day_index, seen)

    picked = []
    newly_seen = []

    for bucket in TOPICS:
        want = slots.get(bucket, 0)
        if want <= 0:
            continue

        available = [p for p in pool.get(bucket, []) if p["id"] not in seen]
        chosen = available[:want]
        picked.extend(chosen)

        chosen_ids = {p["id"] for p in chosen}
        newly_seen.extend(chosen_ids)
        # Drop what we just sent so the pool naturally advances to deeper cuts.
        pool[bucket] = [p for p in pool.get(bucket, []) if p["id"] not in chosen_ids]

    save_pool(pool)
    if skip_seen and newly_seen:
        save_seen(load_seen() + newly_seen)

    return picked


def format_papers_html(papers):
    """Render the linear algebra section for Telegram (parse_mode=HTML)."""
    import html

    def esc(s):
        return html.escape(str(s))

    lines = ["<b>🔢 Linear Algebra</b>"]
    if not papers:
        lines.append("• (no papers today — source unavailable)")
        return lines

    for p in papers:
        authors = p["authors"]
        if len(authors) > 2:
            byline = f"{authors[0]} et al."
        else:
            byline = ", ".join(authors) or "unknown"

        venue = p["venue"]
        if len(venue) > 42:
            venue = venue[:39].rstrip() + "…"

        meta = [f"{p['citations']} citations"]
        if venue:
            meta.append(venue)
        if p["year"]:
            meta.append(str(p["year"]))
        meta.append(BUCKET_LABELS.get(p["bucket"], ""))

        lines.append(f'• <a href="{esc(p["url"])}">{esc(p["title"])}</a>')
        lines.append(f'  <i>{esc(byline)} · {esc(" · ".join(m for m in meta if m))}</i>')

    return lines


if __name__ == "__main__":
    # Manual check: python3 linalg.py [day_index]
    day = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    found = fetch_linalg_papers(day_index=day, skip_seen=False)
    print(f"{len(found)} paper(s) for day_index={day}\n")
    for paper in found:
        print(f"[{paper['bucket']:>9}] {paper['citations']:>5} cit · {paper['year']} · {paper['venue'][:40]}")
        print(f"            {paper['title'][:76]}")
        print(f"            {paper['url']}\n")
