#!/usr/bin/env python3
"""Find intern / new-grad roles on the SWE × math boundary at a watchlist of companies.

LinkedIn has no public jobs API, so the source is the companies themselves: most
post through Greenhouse, Lever or Ashby, and all three expose an anonymous JSON
board. A second source is the monthly Hacker News "Who is hiring?" thread.

The target is engineering roles where the work is geometry, simulation, numerics,
graphics, robotics math, optimization, formal methods or cryptography — not quant,
and not roles that need a master's or PhD. A posting is kept when:

  * the title says intern / new grad / entry level / junior (Ashby's "Intern"
    employment type counts too) and nothing senior, PhD or MBA;
  * it is an engineering or research title, not sales, ops or hardware build;
  * it matches a TOPIC — in the title, or at least two distinct topic terms in
    the description. Companies marked `core` are math-native, so any technical
    early-career role there counts without a topic match;
  * the description doesn't require a PhD or master's;
  * it is in the US or remote.

Jobs already sent are remembered in `.seen_jobs.json`, so each one shows up once.
When nothing new matches, the section is omitted from the digest.
"""

import html
import json
import os
import re
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

USER_AGENT = "daily-digest-bot/1.0 (+https://github.com/)"
TIMEOUT = 60

GREENHOUSE = "https://boards-api.greenhouse.io/v1/boards/{}/jobs"
LEVER = "https://api.lever.co/v0/postings/{}?mode=json"
ASHBY = "https://api.ashbyhq.com/posting-api/job-board/{}"
HN_SEARCH = ("https://hn.algolia.com/api/v1/search_by_date"
             "?tags=story,author_whoishiring&query=who%20is%20hiring")
HN_ITEM = "https://hn.algolia.com/api/v1/items/{}"

MAX_JOBS = int(os.environ.get("JOBS_MAX", "8"))
MAX_HN = int(os.environ.get("JOBS_MAX_HN", "3"))

SEEN_PATH = os.environ.get(
    "JOBS_SEEN_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".seen_jobs.json"),
)
SEEN_LIMIT = 3000

# (display name, ATS, board slug, core). Slugs were checked against each API;
# a slug that stops resolving just logs and is skipped.
COMPANIES = [
    # Math-native: CAD kernels, simulation, formal math, ZK, 3D.
    ("Zoo (CAD)", "greenhouse", "zoo", True),
    ("Shapr3D", "ashby", "shapr3d", True),
    ("PhysicsX", "greenhouse", "physicsx", True),
    ("Neural Concept", "ashby", "neuralconcept", True),
    ("SimScale", "greenhouse", "simscale", True),
    ("Rescale", "ashby", "rescale", True),
    ("Harmonic", "ashby", "harmonic", True),
    ("Axiom Math", "ashby", "axiommath", True),
    ("Axiom (ZK)", "ashby", "axiom", True),
    ("Succinct", "ashby", "succinct", True),
    ("World Labs", "ashby", "worldlabs", True),
    ("Rerun", "ashby", "rerun", True),
    ("Foxglove", "ashby", "foxglove", True),
    ("PsiQuantum", "greenhouse", "psiquantum", True),
    # Graphics, design tools, games.
    ("Figma", "greenhouse", "figma", False),
    ("Epic Games", "greenhouse", "epicgames", False),
    ("Roblox", "greenhouse", "roblox", False),
    ("Mapbox", "ashby", "mapbox", False),
    ("Esri", "greenhouse", "esri", False),
    ("Runway", "ashby", "runway", False),
    ("Pika", "ashby", "pika", False),
    # Autonomy and robotics.
    ("Waymo", "greenhouse", "waymo", False),
    ("Zoox", "lever", "zoox", False),
    ("Nuro", "greenhouse", "nuro", False),
    ("Kodiak", "greenhouse", "kodiak", False),
    ("Motional", "greenhouse", "motional", False),
    ("Wayve", "ashby", "wayve", False),
    ("Applied Intuition", "ashby", "applied", False),
    ("Skydio", "ashby", "skydio", False),
    ("Zipline", "greenhouse", "flyzipline", False),
    ("Physical Intelligence", "ashby", "physicalintelligence", False),
    ("Figure", "greenhouse", "figureai", False),
    ("1X", "ashby", "1x", False),
    ("Agility Robotics", "greenhouse", "agilityrobotics", False),
    ("Path Robotics", "greenhouse", "pathrobotics", False),
    ("Dexterity", "lever", "dexterity", False),
    # Aerospace, defense, manufacturing (GNC, simulation, geometry).
    ("SpaceX", "greenhouse", "spacex", False),
    ("Anduril", "greenhouse", "andurilindustries", False),
    ("Relativity Space", "greenhouse", "relativity", False),
    ("Rocket Lab", "greenhouse", "rocketlab", False),
    ("Astranis", "greenhouse", "astranis", False),
    ("Divergent", "greenhouse", "divergent", False),
    ("Formlabs", "greenhouse", "formlabs", False),
    ("Helion", "ashby", "helion", False),
    # Quantum, chips, compute.
    ("IonQ", "greenhouse", "ionq", False),
    ("Rigetti", "lever", "rigetti", False),
    ("Atom Computing", "lever", "atomcomputing", False),
    ("SandboxAQ", "ashby", "sandboxaq", False),
    ("Tenstorrent", "greenhouse", "tenstorrent", False),
    ("Lightmatter", "greenhouse", "lightmatter", False),
    ("Cerebras", "ashby", "cerebras", False),
    ("Etched", "ashby", "etched", False),
    # AI for science.
    ("Isomorphic Labs", "greenhouse", "isomorphiclabs", False),
    ("Generate Biomedicines", "greenhouse", "generatebiomedicines", False),
    ("Chai Discovery", "ashby", "chaidiscovery", False),
    ("Profluent", "greenhouse", "profluent", False),
    ("Arc Institute", "greenhouse", "arcinstitute", False),
    ("Neuralink", "greenhouse", "neuralink", False),
]

LEVEL = re.compile(
    r"\bintern(ship)?s?\b|\bco-?op\b|new[- ]grad|new graduate|university grad|"
    r"recent grad|early[- ]career|entry[- ]level|\bjunior\b|\bjr\.?\s|"
    r"\b(engineer|developer|scientist) i\b|\b202[6-8]\b",
    re.I,
)
TOO_SENIOR = re.compile(
    r"\b(senior|sr\.?|staff|principal|lead|manager|director|head|vp|architect|"
    r"ii|iii|iv)\b|ph\.?d|doctoral|postdoc|\bmba\b|master'?s",
    re.I,
)
NON_TECH = re.compile(
    r"marketing|sales|finance|accounting|recruit|talent|legal|counsel|\bpeople\b|"
    r"\bhr\b|business|communications|policy|supply chain|procurement|buyer|"
    r"operations|technician|machinist|mechanic|welder|assembl|manufacturing|"
    r"production|facilities|\behs\b|quality|customer|account|program manager|"
    r"product manager|writer|content|brand|events|assistant|treasury|\btax\b|"
    r"payroll|strategy|investor|partnership|mechanical|electrical|propulsion|"
    r"structur|avionics|hardware|\brf\b|thermal|materials|chemical|civil|"
    r"industrial|mechatronic|reliability|supplier|launch|test engineer|"
    r"\bdesigner\b|\bart(ist)?\b|animator|\bux\b|trading|quant|firmware|"
    r"embedded|\bic\b|asic|fpga|silicon|packaging|circuit|harness|warhead|"
    r"maintenance|tooling|field|mission|validation|integration|security",
    re.I,
)
# At broad companies the title itself has to name the math, because descriptions
# carry company boilerplate ("computer vision", "sensor fusion") on every posting.
# "CAD" is left out: at hardware companies a CAD engineer does mechanical design.
MATHY_TITLE = re.compile(
    r"computational|physics|geometr|simulation|solver|numerical|graphics|"
    r"rendering|\b3d\b|vision|perception|autonomy|controls?\b|\bgnc\b|"
    r"guidance|planning|estimation|optimization|kernel|compiler|research|"
    r"cryptograph|verification|algorithm|math|robotics",
    re.I,
)
TECH = re.compile(
    r"software|engineer|developer|research|scientist|algorithm|math|"
    r"computational|simulation|graphics|robotics|autonomy|perception|gnc|"
    r"machine learning|\bml\b|\bai\b|compiler|kernel",
    re.I,
)
REQUIRES_GRAD = re.compile(
    r"(pursuing|enrolled in|candidates? (for|in)|currently in)\s+(a |an )?"
    r"(ph\.?d|doctoral|master'?s|m\.?s\.? (or|/) ph\.?d)|"
    r"ph\.?d\.? (students?|candidates?) (only|required)|"
    r"(ph\.?d|master'?s|m\.?s\.?)( degree)? (is )?required|"
    r"requires? an? (ph\.?d|master'?s)|must (have|hold) an? (ph\.?d|master'?s)",
    re.I,
)

# Topic -> regex. Order sets the label shown when a job matches several.
TOPICS = {
    "geometry": r"computational geometry|geometry (kernel|processing|engine)|"
                r"geometric (modeling|algorithms?|computing)|\bmesh(ing|es)?\b|"
                r"\bcad\b|b-rep|nurbs|point clouds?|surface reconstruction",
    "simulation": r"simulation|\bsolvers?\b|finite[- ]element|\bfem\b|\bfea\b|\bcfd\b|"
                  r"fluid dynamics|\bpdes?\b|partial differential|numerical "
                  r"(methods|analysis|linear algebra|simulation)|scientific computing|"
                  r"physics engine",
    "graphics": r"rendering|computer graphics|\bgraphics\b|gaussian splat|\bnerf\b|"
                r"3d reconstruction|\bslam\b|computer vision|ray tracing|shaders?",
    "robotics math": r"motion planning|trajectory optimization|state estimation|"
                     r"kalman|guidance,? navigation|\bgnc\b|control theory|"
                     r"optimal control|kinematics|sensor fusion",
    "optimization": r"convex optimization|mathematical optimization|"
                    r"(linear|integer|mixed[- ]integer) programming|"
                    r"operations research|combinatorial optimization",
    "formal methods": r"formal (verification|methods)|theorem prov|lean ?4|"
                      r"lean prover|mathlib|\bcoq\b|isabelle|proof assistant|"
                      r"type theory",
    "crypto": r"cryptograph|zero[- ]knowledge|\bzk(vm|p)?\b|\bsnarks?\b|"
              r"elliptic curve|homomorphic",
    "GPU/HPC": r"\bcuda\b|gpu kernels?|\bhpc\b|high[- ]performance computing|"
               r"numerical librar",
}
TOPIC_RES = {name: re.compile(rx, re.I) for name, rx in TOPICS.items()}

NON_US = re.compile(
    r"london|\buk\b|united kingdom|england|germany|munich|berlin|zurich|"
    r"switzerland|lausanne|paris|france|japan|tokyo|india|bangalore|bengaluru|"
    r"hyderabad|canada|toronto|vancouver|montreal|israel|tel aviv|budapest|"
    r"hungary|poland|warsaw|krakow|australia|sydney|melbourne|singapore|korea|"
    r"seoul|taiwan|taipei|china|shanghai|beijing|netherlands|amsterdam|ireland|"
    r"dublin|spain|madrid|barcelona|sweden|stockholm|denmark|copenhagen|mexico|"
    r"brazil|new zealand|auckland|italy|belgium|austria|vienna|czech|prague|"
    r"romania|ukraine|estonia|finland|norway|portugal|lisbon|argentina|serbia|"
    r"belgrade|philippines|vietnam|hong kong|dubai|emea|apac",
    re.I,
)
# Explicit markers only: "Remote" alone often means remote within Europe.
US = re.compile(r"united states|\busa?\b|\bu\.s\.|, [A-Z]{2}\b")
BAY_AREA = re.compile(
    r"san francisco|bay area|palo alto|mountain view|menlo park|redwood city|"
    r"san mateo|sunnyvale|santa clara|san jose|cupertino|oakland|berkeley|"
    r"emeryville|foster city|burlingame|san carlos|fremont|hayward|alameda|"
    r"milpitas|los altos|belmont|brisbane|san bruno|newark, ca",
    re.I,
)


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _text(s):
    """HTML (possibly entity-escaped, as Greenhouse sends it) to plain text."""
    s = html.unescape(html.unescape(s or ""))
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s))


def _ts(raw):
    """ISO string or epoch millis to an aware datetime; None if missing."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(raw / 1000, timezone.utc)
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def _job(company, key, title, location, url, posted, description, tagged_intern=False):
    return {"company": company, "id": key, "ids": [key], "title": title.strip(),
            "location": location or "", "url": url, "posted": posted,
            "description": description, "tagged_intern": tagged_intern}


def _title_ok(job):
    """The checks that need only the title, so big boards can skip descriptions."""
    title = job["title"]
    return ((LEVEL.search(title) or job["tagged_intern"])
            and not TOO_SENIOR.search(title)
            and not NON_TECH.search(title)
            and TECH.search(title))


def _from_greenhouse(name, slug):
    # The full board with descriptions is 30-45 MB for SpaceX and Anduril, so list
    # titles first and fetch descriptions only for jobs that pass the title checks.
    jobs = []
    for j in _get(GREENHOUSE.format(slug))["jobs"]:
        job = _job(name, f"gh:{slug}:{j['id']}", j["title"],
                   (j.get("location") or {}).get("name"), j["absolute_url"],
                   _ts(j.get("first_published") or j.get("updated_at")), "")
        if _title_ok(job):
            detail = _get(f"{GREENHOUSE.format(slug)}/{j['id']}")
            job["description"] = _text(detail.get("content"))
            jobs.append(job)
    return jobs


def _from_lever(name, slug):
    jobs = []
    for j in _get(LEVER.format(slug)):
        cats = j.get("categories") or {}
        location = ", ".join(filter(None, [cats.get("location"), j.get("country")]))
        jobs.append(_job(
            name, f"lever:{slug}:{j['id']}", j["text"], location, j["hostedUrl"],
            _ts(j.get("createdAt")),
            " ".join(filter(None, [j.get("descriptionPlain"), j.get("additionalPlain")]
                            + [_text(l.get("content")) for l in j.get("lists") or []])),
            tagged_intern="intern" in (cats.get("commitment") or "").lower(),
        ))
    return jobs


def _from_ashby(name, slug):
    jobs = []
    for j in _get(ASHBY.format(slug))["jobs"]:
        if j.get("isListed") is False:
            continue
        country = (((j.get("address") or {}).get("postalAddress") or {})
                   .get("addressCountry") or "")
        location = ", ".join(filter(None, [
            j.get("location"), country, "Remote" if j.get("isRemote") else ""]))
        jobs.append(_job(
            name, f"ashby:{slug}:{j['id']}", j["title"], location, j["jobUrl"],
            _ts(j.get("publishedAt")), j.get("descriptionPlain") or "",
            tagged_intern=j.get("employmentType") == "Intern",
        ))
    return jobs


FETCHERS = {"greenhouse": _from_greenhouse, "lever": _from_lever, "ashby": _from_ashby}


def _topics(text):
    return [name for name, rx in TOPIC_RES.items() if rx.search(text)]


def _description_topics(text):
    """Topics from the description, counted only with two distinct term hits.

    One passing mention ("experience with simulation a plus") is too weak; two
    different terms across the topics means the job is actually about it.
    """
    terms = set()
    for rx in TOPIC_RES.values():
        terms.update(m.group(0).lower() for m in rx.finditer(text))
    return _topics(text) if len(terms) >= 2 else []


def _us_or_remote(location):
    return not location or not NON_US.search(location) or bool(US.search(location))


def _board_matches(company, ats, slug, core):
    try:
        jobs = FETCHERS[ats](company, slug)
    except (urllib.error.URLError, json.JSONDecodeError, KeyError, ValueError) as e:
        print(f"[jobs] {company} ({ats}:{slug}) failed: {e}", file=sys.stderr)
        return []

    matches = []
    for job in jobs:
        if not _title_ok(job) or not _us_or_remote(job["location"]):
            continue
        if REQUIRES_GRAD.search(job["description"]):
            continue
        if not core and not MATHY_TITLE.search(job["title"]):
            continue
        topics = _topics(job["title"]) or _description_topics(job["description"])
        if not topics and not core:
            continue
        job["topics"] = topics
        matches.append(job)
    return matches


def _latest_hn_thread():
    for hit in _get(HN_SEARCH)["hits"]:
        if hit["title"].lower().startswith("ask hn: who is hiring"):
            return hit
    return None


def _hn_matches(now):
    """Early-career, on-topic posts from this month's "Who is hiring?" thread."""
    try:
        thread = _latest_hn_thread()
        # A thread older than ~5 weeks means this month's isn't up yet; its posts
        # were offered last month already.
        if not thread or now - _ts(thread["created_at"]) > timedelta(days=35):
            return []
        comments = _get(HN_ITEM.format(thread["objectID"]))["children"]
    except (urllib.error.URLError, json.JSONDecodeError, KeyError, ValueError) as e:
        print(f"[jobs] HN who-is-hiring failed: {e}", file=sys.stderr)
        return []

    matches = []
    for c in comments:
        text = _text(c.get("text"))
        if not text or not LEVEL.search(text) or REQUIRES_GRAD.search(text):
            continue
        # By convention the first line is "Company | Role | Location | ...".
        first = _text(re.split(r"<p>", c.get("text") or "")[0])
        if not _us_or_remote(first):
            continue
        if re.search(r"trading|hedge fund|quantitative (trader|researcher)", text, re.I):
            continue
        topics = _description_topics(text)
        if not topics:
            continue
        head = first if len(first) <= 110 else first[:107].rstrip() + "…"
        matches.append(_job("HN", f"hn:{c['id']}", head, "",
                            f"https://news.ycombinator.com/item?id={c['id']}",
                            _ts(c.get("created_at")), text))
        matches[-1]["topics"] = topics
    return matches


def load_seen():
    """Ids already sent. Missing or corrupt cache is not fatal — just start over."""
    try:
        with open(SEEN_PATH) as fh:
            return list(json.load(fh).get("ids", []))
    except (OSError, json.JSONDecodeError, AttributeError):
        return []


def mark_seen(jobs):
    """Record jobs as sent. Call only after the digest actually went out."""
    ids = load_seen() + [i for j in jobs for i in j["ids"]]
    try:
        with open(SEEN_PATH, "w") as fh:
            json.dump({"ids": ids[-SEEN_LIMIT:]}, fh)
    except OSError as e:
        print(f"[jobs] could not persist seen-cache: {e}", file=sys.stderr)


def _dedupe(jobs):
    """Collapse one role posted per season or per city into a single entry.

    The ids of every copy are kept, so marking the entry seen covers all of them.
    """
    out = {}
    for job in jobs:
        key = (job["company"], re.sub(
            r"[\s(,-]*\b((spring|summer|fall|winter|jan|may|june|dec)\w*\s*)?"
            r"(&\s*)?'?(20)?2[6-8]\b\)?", "", job["title"], flags=re.I).strip(" -,").lower())
        if key in out:
            out[key]["ids"].extend(job["ids"])
        else:
            out[key] = job
    return list(out.values())


def fetch_jobs(skip_seen=True, now=None):
    """New matching jobs: at most MAX_JOBS from company boards plus MAX_HN from HN.

    Bay Area first, then newest. Anything left over stays unseen, so it is
    offered again on a later day.
    """
    now = now or datetime.now(timezone.utc)
    seen = set(load_seen()) if skip_seen else set()

    with ThreadPoolExecutor(max_workers=8) as pool:
        boards = list(pool.map(lambda c: _board_matches(*c), COMPANIES))
    board_jobs = _dedupe([j for jobs in boards for j in jobs if j["id"] not in seen])
    hn_jobs = [j for j in _hn_matches(now) if j["id"] not in seen]

    oldest = datetime.min.replace(tzinfo=timezone.utc)
    board_jobs.sort(key=lambda j: (not BAY_AREA.search(j["location"]),
                                   -(j["posted"] or oldest).timestamp()))
    hn_jobs.sort(key=lambda j: -(j["posted"] or oldest).timestamp())
    return board_jobs[:MAX_JOBS] + hn_jobs[:MAX_HN]


def _short_location(location):
    parts = [p.strip() for p in re.split(r"[;|/]|, (?=[A-Z][a-z])", location) if p.strip()]
    return parts[0] + (f" +{len(parts) - 1}" if len(parts) > 1 else "") if parts else ""


def format_jobs_html(jobs):
    """Render the jobs section for Telegram (parse_mode=HTML)."""
    def esc(s):
        return html.escape(str(s))

    lines = ["<b>🧮 Jobs</b>"]
    for j in jobs:
        if j["company"] == "HN":
            lines.append(f'• <a href="{esc(j["url"])}">{esc(j["title"])}</a>')
            meta = ["HN who's hiring", ", ".join(j["topics"])]
        else:
            lines.append(f'• <a href="{esc(j["url"])}">{esc(j["title"])}</a> '
                         f'— {esc(j["company"])}')
            meta = [_short_location(j["location"]), ", ".join(j["topics"])]
        lines.append(f'  <i>{esc(" · ".join(m for m in meta if m))}</i>')
    return lines


if __name__ == "__main__":
    # Manual check, ignores the seen-cache: python3 jobs.py
    found = fetch_jobs(skip_seen=False)
    print(f"{len(found)} job(s)\n")
    print("\n".join(format_jobs_html(found)))
