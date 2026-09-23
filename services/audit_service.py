"""Free Automation Opportunity Audit — BFN's lead magnet.

A business owner gives us their website; we fetch it, check it for the
concrete things that lose local businesses customers (no booking link, no
click-to-call, nothing answering after hours, no review ask...), score it,
and turn the gaps into a short report where every gap maps to the Workframe
piece that fixes it.

The score and findings are rule-based from what's actually on the page — the
model only rewrites them into plain, specific language. It is told never to
invent numbers, and if it fails the rule-based report goes out as-is.
"""

import ipaddress
import json
import re
import socket
import time
from urllib.parse import urljoin, urlparse

import httpx

from services.workframe_agents import _get_client

MAX_BYTES = 1_500_000
FETCH_TIMEOUT = 10
MAX_REDIRECTS = 4
USER_AGENT = "Mozilla/5.0 (compatible; BFN-Audit/1.0; +https://brand-factory-frontend.vercel.app/audit)"

# Hosts that are someone else's platform, not the business's own website.
# Fetching them is pointless (login walls / JS apps) — and "your only web
# presence is an Instagram page" is itself the finding.
PLATFORM_HOSTS = (
    "instagram.com", "facebook.com", "tiktok.com", "linktr.ee", "booksy.com",
    "vagaro.com", "styleseat.com", "fresha.com", "yelp.com", "google.com",
    "g.page", "square.site", "glossgenius.com", "x.com", "twitter.com",
)

BOOKING_PATTERNS = (
    "booksy", "vagaro", "square.site", "squareup.com/appointments", "glossgenius", "fresha",
    "schedulicity", "calendly", "acuityscheduling", "styleseat", "setmore", "mindbodyonline",
    "opentable", "resy.com", "tock", "housecallpro", "jobber", "book now", "book online",
    "schedule now", "book an appointment", "make a reservation", "reserve",
)
ORDERING_PATTERNS = ("toasttab", "doordash", "grubhub", "ubereats", "chownow", "order online", "order now")
CHAT_PATTERNS = (
    "tawk.to", "intercom", "drift.com", "crisp.chat", "tidio", "livechat", "podium",
    "birdeye", "zendesk", "hubspot", "messenger", "manychat", "bfn-widget",
)
REVIEW_PATTERNS = ("review", "testimonial", "yelp.com", "what our clients say", "5 stars", "★")
EMAIL_CAPTURE_PATTERNS = ("newsletter", "subscribe", "mailchimp", "klaviyo", "join our list", "sign up for")


META_REFRESH = re.compile(r"<meta[^>]+http-equiv=[\"']?refresh[\"']?[^>]+content=[\"']?\s*\d+\s*;\s*url=([^\"'>]+)", re.I)

# Below this many words of visible text we can't honestly judge the page's
# content (it's probably rendered by JavaScript) — only head-level checks count.
MIN_READABLE_WORDS = 60
HEAD_CHECKS = ("https", "mobile_friendly", "meta_description")


class AuditError(Exception):
    """User-facing audit failure (bad URL, unreachable site...)."""


def _normalize_url(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        raise AuditError("Enter your website address.")
    if not re.match(r"^https?://", raw, re.I):
        raw = "https://" + raw
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or "." not in parsed.hostname:
        raise AuditError("That doesn't look like a website address.")
    return raw


def _is_platform(host: str) -> bool:
    host = host.lower()
    return any(host == p or host.endswith("." + p) for p in PLATFORM_HOSTS)


def _assert_public_host(host: str, port: int) -> None:
    """SSRF guard: every address the hostname resolves to must be a public
    IP. Checked again on every redirect hop."""
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise AuditError("We couldn't find that website — double-check the address.")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global or ip.is_multicast:
            raise AuditError("That address isn't a public website.")


class CertificateProblem(Exception):
    """The site's HTTPS certificate is broken — visitors see a browser
    security warning. That's a finding, not a reason to fail the audit."""


async def _fetch(url: str, verify: bool = True) -> tuple[str, str, int, float]:
    """Returns (final_url, html, status_code, seconds). Follows redirects
    manually so each hop gets the SSRF check."""
    started = time.monotonic()
    async with httpx.AsyncClient(timeout=FETCH_TIMEOUT, follow_redirects=False, verify=verify, headers={"User-Agent": USER_AGENT}) as client:
        for _ in range(MAX_REDIRECTS + 1):
            parsed = urlparse(url)
            _assert_public_host(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
            try:
                async with client.stream("GET", url) as response:
                    if response.status_code in (301, 302, 303, 307, 308) and response.headers.get("location"):
                        url = urljoin(url, response.headers["location"])
                        if urlparse(url).scheme not in ("http", "https"):
                            raise AuditError("That website redirects somewhere we can't check.")
                        continue
                    body = b""
                    async for chunk in response.aiter_bytes():
                        body += chunk
                        if len(body) > MAX_BYTES:
                            break
                    html = body.decode(response.encoding or "utf-8", errors="replace")
                    # Old-school <meta http-equiv="refresh"> redirects are
                    # common on small-business sites — treat them like a 30x.
                    refresh = META_REFRESH.search(html[:5000])
                    if refresh and response.status_code < 400:
                        url = urljoin(url, refresh.group(1).strip("'\" "))
                        if urlparse(url).scheme not in ("http", "https"):
                            raise AuditError("That website redirects somewhere we can't check.")
                        continue
                    return url, html, response.status_code, time.monotonic() - started
            except httpx.ConnectError as exc:
                if verify and "CERTIFICATE_VERIFY_FAILED" in str(exc):
                    raise CertificateProblem(str(exc))
                raise AuditError("We couldn't load that website — it may be down or blocking visitors.")
            except httpx.HTTPError:
                raise AuditError("We couldn't load that website — it may be down or blocking visitors.")
    raise AuditError("That website redirects too many times.")


def _has(text: str, patterns) -> bool:
    return any(p in text for p in patterns)


def extract_signals(final_url: str, html: str, status_code: int, seconds: float) -> dict:
    lower = html.lower()
    hrefs = " ".join(re.findall(r'href\s*=\s*["\']([^"\']+)', lower))
    title = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    desc = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']*)', html, re.I)
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    forms = re.findall(r"<form.*?</form>", lower, re.S)

    return {
        "final_url": final_url,
        "status_code": status_code,
        "load_seconds": round(seconds, 2),
        "page_kb": round(len(html) / 1024),
        "https": final_url.lower().startswith("https://"),
        "mobile_friendly": 'name="viewport"' in lower or "name='viewport'" in lower,
        "title": (title.group(1).strip()[:120] if title else None),
        "meta_description": bool(desc and desc.group(1).strip()),
        "click_to_call": "tel:" in hrefs,
        "email_link": "mailto:" in hrefs,
        "booking": _has(lower, BOOKING_PATTERNS),
        "online_ordering": _has(lower, ORDERING_PATTERNS),
        "contact_form": any(("type=\"email\"" in f or "type='email'" in f or "<textarea" in f or "type=\"tel\"" in f) for f in forms),
        "chat": _has(lower, CHAT_PATTERNS),
        "reviews_shown": _has(lower, REVIEW_PATTERNS),
        "google_maps": "maps.google" in lower or "google.com/maps" in lower or "g.page" in lower or "goo.gl/maps" in lower,
        "email_capture": _has(lower, EMAIL_CAPTURE_PATTERNS),
        "instagram": "instagram.com/" in hrefs,
        "facebook": "facebook.com/" in hrefs,
        "tiktok": "tiktok.com/" in hrefs,
        "analytics": "googletagmanager" in lower or "gtag(" in lower or "fbq(" in lower,
        "word_count": len(text.split()),
        "text_sample": text[:2500],
    }


# Each check: (signal, points, finding-if-missing, fix, workframe piece, impact).
# Points sum to 100. Ordered roughly by how much money the gap costs a
# local appointment/walk-in business.
CHECKS = (
    ("booking", 16, "No way to book online from the site.", "Add a booking link and have every inquiry pointed to it automatically.", "Lead Agent", "high"),
    ("chat", 14, "Nothing answers visitors after hours — questions at 10pm wait until morning (or go to a competitor).", "An instant-reply assistant on the site that answers from your real prices/hours and captures the lead.", "Lead Agent", "high"),
    ("click_to_call", 10, "Phone number isn't tap-to-call on mobile.", "Make the number a tap-to-call link — most local searches happen on phones.", "Website fix", "medium"),
    ("contact_form", 8, "No contact form — a visitor who won't call has no way to reach you.", "Capture every inquiry into one inbox with automatic follow-up if they go quiet.", "Sales / Follow-up Agent", "high"),
    ("reviews_shown", 10, "No reviews or testimonials on the site.", "Automatically ask every customer for a Google review after their visit, then show them off.", "Reputation Agent", "high"),
    ("mobile_friendly", 10, "The site isn't set up for phones.", "Mobile layout fix.", "Website fix", "high"),
    ("https", 6, "The site isn't secure (no HTTPS) — browsers warn visitors away.", "Turn on HTTPS.", "Website fix", "medium"),
    ("meta_description", 5, "No search description — Google shows a random snippet instead of your pitch.", "Write a proper search description.", "Website fix", "low"),
    ("email_capture", 6, "No way to stay in touch with visitors who aren't ready yet.", "Collect numbers/emails and send reminders and offers automatically.", "Sales / Follow-up Agent", "medium"),
    ("instagram", 5, "Site doesn't link your socials.", "Link your Instagram/TikTok so visitors can see your work.", "Content Agent", "low"),
    ("google_maps", 5, "No map / Google listing link.", "Link your Google Business Profile so people can find you and leave reviews.", "Reputation Agent", "low"),
    ("analytics", 5, "No visitor tracking — no way to know what's working.", "Add basic analytics and a monthly results summary.", "CEO Agent", "low"),
)


def score_and_findings(signals: dict) -> tuple[int, list[dict]]:
    """Score out of 100 over the checks we could actually evaluate. When the
    page text is unreadable (JS-rendered), content checks are skipped rather
    than reported as missing — a wrong "you have no booking link" in a sales
    audit costs more trust than it could ever win."""
    limited = signals.get("word_count", 0) < MIN_READABLE_WORDS
    signals["limited"] = limited
    checks = [c for c in CHECKS if not limited or c[0] in HEAD_CHECKS]
    possible = sum(c[1] for c in checks)
    earned = 0
    findings = []
    for key, points, finding, fix, piece, impact in checks:
        if signals.get(key):
            earned += points
        else:
            findings.append({"check": key, "finding": finding, "fix": fix, "workframe": piece, "impact": impact})
    score = round(100 * earned / possible) if possible else 0
    if limited:
        findings.append({
            "check": "readable",
            "finding": "We could only read a little of your page — it likely loads with JavaScript, so search engines and quick visitors may see very little of it.",
            "fix": "Make sure your key info — services, prices, booking link — is in the page itself.",
            "workframe": "Website fix", "impact": "medium",
        })
    if signals.get("load_seconds", 0) > 4:
        score = max(0, score - 5)
        findings.append({
            "check": "speed",
            "finding": f"The page took {signals['load_seconds']}s to load — people leave after ~3s.",
            "fix": "Compress images and trim heavy scripts.", "workframe": "Website fix", "impact": "medium",
        })
    impact_rank = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: impact_rank[f["impact"]])
    return score, findings


def no_website_report(url: str) -> tuple[int, dict, list[dict]]:
    host = urlparse(url).hostname or url
    signals = {"final_url": url, "own_website": False, "platform": host}
    findings = [
        {"check": "own_website", "finding": f"Your main web presence is {host} — a page you rent, not one you own.", "fix": "A simple one-page site with booking, prices, and reviews that shows up on Google.", "workframe": "Website fix", "impact": "high"},
        {"check": "chat", "finding": "Nothing answers people after hours — DMs and comments wait until you see them.", "fix": "An instant-reply assistant that answers from your real prices/hours and captures the lead.", "workframe": "Lead Agent", "impact": "high"},
        {"check": "reviews_shown", "finding": "No automatic review asks.", "fix": "Every customer gets a Google review request after their visit.", "workframe": "Reputation Agent", "impact": "high"},
        {"check": "contact_form", "finding": "Leads who don't book right away are never followed up with.", "fix": "Automatic follow-up texts for anyone who asked but didn't book.", "workframe": "Sales / Follow-up Agent", "impact": "high"},
    ]
    return 20, signals, findings


async def _narrate(business: dict, signals: dict, score: int, findings: list[dict]) -> dict | None:
    """Rewrites the rule-based findings into a specific, plain-English report.
    Returns None on any failure (caller uses the rule-based version)."""
    prompt = (
        "You write short, specific website audits for small local business owners. Plain language, no jargon, "
        "no hype, and never use the words 'AI' or 'artificial intelligence'. Never invent statistics, "
        "percentages, or dollar amounts. Only use the findings given.\n\n"
        f"Business: {business.get('business_name') or 'unknown'} — {business.get('business_type') or 'local business'} "
        f"in {business.get('city') or 'New Orleans'}\n"
        f"Score: {score}/100\n"
        f"Findings (already ranked by impact): {json.dumps(findings)}\n"
        f"Page text sample: {signals.get('text_sample', '')[:1500]}\n\n"
        "Return JSON: {\"headline\": string (<=12 words), \"summary\": string (2 sentences, name one thing they "
        "do well if the page text shows it), \"opportunities\": [{\"title\": string (<=8 words), \"why\": string "
        "(1 sentence, specific to this business), \"fix\": string (1 sentence)}] — one per finding, same order, max 6}"
    )
    try:
        response = await _get_client().chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4,
            max_tokens=900,
            response_format={"type": "json_object"},
        )
        parsed = json.loads(response.choices[0].message.content)
        if not isinstance(parsed.get("opportunities"), list) or not parsed.get("headline"):
            return None
        return parsed
    except Exception as exc:  # noqa: BLE001
        print(f"[audit] narration failed, using rule-based report: {exc!r}")
        return None


def _rule_based_report(score: int, findings: list[dict]) -> dict:
    if score >= 75:
        headline = "Solid foundation — a few gaps are costing you customers"
    elif score >= 45:
        headline = "You're leaving bookings on the table"
    else:
        headline = "Customers are slipping through the cracks"
    return {
        "headline": headline,
        "summary": f"We found {len(findings)} gaps between someone finding you and actually booking. The top ones are below.",
        "opportunities": [{"title": f["finding"].split(" — ")[0].rstrip("."), "why": f["finding"], "fix": f["fix"]} for f in findings[:6]],
    }


async def run_audit(website_url: str, business: dict) -> dict:
    """Returns {"score", "signals", "report"} where report.opportunities[i]
    carries the rule-based impact + workframe mapping for findings[i]."""
    url = _normalize_url(website_url)
    host = urlparse(url).hostname

    if _is_platform(host):
        score, signals, findings = no_website_report(url)
    else:
        cert_broken = False
        try:
            final_url, html, status_code, seconds = await _fetch(url)
        except CertificateProblem:
            cert_broken = True
            final_url, html, status_code, seconds = await _fetch(url, verify=False)
        if status_code >= 400:
            raise AuditError(f"That website returned an error ({status_code}) — double-check the address.")
        signals = extract_signals(final_url, html, status_code, seconds)
        signals["own_website"] = True
        signals["certificate_broken"] = cert_broken
        if cert_broken:
            signals["https"] = False
        score, findings = score_and_findings(signals)
        if cert_broken:
            for f in findings:
                if f["check"] == "https":
                    f.update({
                        "finding": "Your site's security certificate is broken — browsers show visitors a 'Not secure' warning before your page loads.",
                        "fix": "Fix the HTTPS certificate so the warning goes away.",
                        "impact": "high",
                    })
            impact_rank = {"high": 0, "medium": 1, "low": 2}
            findings.sort(key=lambda f: impact_rank[f["impact"]])

    report = await _narrate(business, signals, score, findings) or _rule_based_report(score, findings)
    opportunities = report.get("opportunities", [])[: min(6, len(findings))]
    for opp, finding in zip(opportunities, findings):
        opp["impact"] = finding["impact"]
        opp["workframe"] = finding["workframe"]
    report["opportunities"] = opportunities

    signals.pop("text_sample", None)
    return {"score": score, "signals": signals, "report": report}
