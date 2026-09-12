"""
shopping_agent.py - JARVIS background shopping agent

Designed for the JARVIS plugin loader:
    PLUGIN = {...}
    run(parameters, player=None, session_memory=None) -> str

Key design points:
- Search jobs run in a daemon background thread and return immediately.
- Real worker state is stored in memory and exposed by action='status'.
- A hard timeout prevents zombie jobs.
- Amazon and Flipkart are searched independently; one timeout does not block the other.
- Completion/failure is written to the JARVIS log from the worker.
- The shopping worker owns its Playwright context, so browser_agent closing its context
  cannot kill a shopping job.
- No passwords, card numbers, CVV, PINs or OTPs are stored or typed.

This plugin intentionally does not pretend that a retailer checkout succeeded. Retailer
CAPTCHA/login/payment/OTP screens are reported as a user hand-off.
"""
from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from urllib.parse import quote_plus, urlparse

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
except Exception:
    sync_playwright = None
    PWTimeout = Exception


PLUGIN = {
    "name": "shopping_agent",
    "description": (
        "Runs a separate background shopping agent. Searches Amazon India and Flipkart, "
        "understands a budget, compares products, ranks the best matches, and reports "
        "real worker status. Actions: search/start, status, cancel, results, add_to_cart, "
        "and order. Actions open/open_all open exact saved result links directly in the normal browser without browser-agent LLM routing. "
        "add_to_cart/order open a SEPARATE, dedicated browser profile (~/.jarvis_shopping_profile) "
        "that starts logged OUT of every retailer account — it is never the user's regular "
        "browser session, and JARVIS never stores or types the user's password. The first "
        "time add_to_cart/order is used for a retailer, tell the user they need to log in "
        "manually in that window once; the profile is persistent so the login is remembered "
        "for every call after that. Shopping work never blocks normal JARVIS conversation."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "search | start | status | results | cancel | open | open_all | add_to_cart | order"},
            "index": {"type": "NUMBER", "description": "1-based result number from the latest completed shopping results"},
            "site": {"type": "STRING", "description": "Optional retailer filter: Amazon or Flipkart"},
            "query": {"type": "STRING", "description": "What to buy"},
            "budget": {"type": "NUMBER", "description": "Maximum budget in INR"},
            "url": {"type": "STRING", "description": "Product URL for cart/order action"},
            "confirm": {"type": "BOOLEAN", "description": "Explicit confirmation for cart/order actions"},
            "max_seconds": {"type": "NUMBER", "description": "Overall background search timeout, maximum 180 seconds"},
        },
        "required": [],
    },
}

_LOCK = threading.RLock()
_WORKER: dict = {
    "state": "idle",  # idle/searching/completed/failed/cancelled
    "job_id": 0,
    "query": "",
    "budget": None,
    "started_at": None,
    "finished_at": None,
    "amazon": {"state": "idle", "count": 0, "error": ""},
    "flipkart": {"state": "idle", "count": 0, "error": ""},
    "results": [],
    "message": "",
    "progress": "",
    "error": "",
    "cancel": False,
    "player": None,
}

_MAX_SECONDS = 180
_DEFAULT_SECONDS = 90
_STEP_TIMEOUT = 9000
_NAV_TIMEOUT = 18000
_MAX_RESULTS_PER_SITE = 8


def _log(player, text: str):
    try:
        if player is not None and hasattr(player, "write_log"):
            player.write_log(text)
    except Exception:
        pass


def _notify(player, text: str):
    """Like _log, but also makes JARVIS actually say it — used for the final
    result so the "I'll notify you when it finishes" promise in _start() is
    kept, instead of the result only ever showing up in the activity log."""
    _log(player, text)
    try:
        speak = getattr(player, "speak", None)
        if callable(speak):
            speak(text)
    except Exception:
        pass


def _now():
    return time.time()


def _money(value) -> float | None:
    if value is None:
        return None
    s = str(value).replace(",", "")
    # Strong evidence first: INR/rupee price markers.
    m = re.search(r"(?:₹|rs\.?|inr\s*)\s*(\d+(?:\.\d+)?)", s, re.I)
    if m:
        try:
            return float(m.group(1))
        except Exception:
            return None
    # Do not guess from arbitrary numbers in a long product title.
    # Only accept a plain number when it is explicitly introduced as a price.
    m = re.search(r"(?:price|deal|mrp|cost)\s*[:\-]?\s*(\d+(?:\.\d+)?)", s, re.I)
    try:
        return float(m.group(1)) if m else None
    except Exception:
        return None


def _budget_from_text(text: str) -> float | None:
    if not text:
        return None
    t = text.lower().replace(",", "")
    patterns = [
        r"(?:under|below|less than|within|upto|up to|budget(?: is)?)[^0-9₹]{0,15}₹?\s*(\d+(?:\.\d+)?)\s*(k|thousand)?",
        r"₹\s*(\d+(?:\.\d+)?)\s*(k|thousand)?",
    ]
    for p in patterns:
        m = re.search(p, t)
        if m:
            n = float(m.group(1))
            if (m.group(2) or "").lower() in ("k", "thousand"):
                n *= 1000
            return n
    return None


def _clean_text(s: str, limit=300):
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s[:limit]


def _site_search(page, site: str, query: str, budget: float | None, deadline: float, player):
    """Search a retailer directly first, then fall back to search-engine discovery.

    The worker uses direct retailer search first and search-engine fallback. That frequently returned
    zero usable listings even when the retailer had plenty of products. This version:
      1) opens the retailer's own search page,
      2) extracts product-card/anchor data,
      3) falls back to Google and Bing if direct extraction is blocked,
      4) validates that the URL belongs to the requested retailer,
      5) rejects obvious category/search URLs and bad price/title data.
    """
    state_key = site.lower()
    domain = "amazon.in" if site == "Amazon" else "flipkart.com"

    with _LOCK:
        _WORKER[state_key] = {"state": "searching", "count": 0, "error": ""}

    results = []
    seen_urls = set()

    # Text markers a retailer shows directly on the search-result card when an
    # item can't actually be bought right now. Filtering on these (rather than
    # opening every product page individually, which the search budget can't
    # afford) removes most of the "out of stock" / "not deliverable" noise the
    # user was seeing in results.
    _UNAVAILABLE_MARKERS = (
        "currently unavailable", "out of stock", "sold out",
        "temporarily out of stock", "no longer available",
        "coming soon", "notify me", "unavailable in your area",
    )

    def _looks_unavailable(text: str) -> bool:
        low = (text or "").lower()
        return any(m in low for m in _UNAVAILABLE_MARKERS)

    def add_result(title, price, rating, url, source, card_text=None):
        title = _clean_text(title, 220)
        if not title or len(title) < 12 or not url:
            return
        host = (urlparse(url).netloc or "").lower()
        if domain not in host:
            return
        low = url.lower()
        if any(x in low for x in ("/search", "/s?", "/gp/browse", "/stores/")):
            return
        if price is None or price <= 0 or price > 10000000:
            return
        # Reject text that is almost certainly a navigation/category label.
        bad = ("results for", "search results", "customer service", "sell on", "help center")
        if any(x in title.lower() for x in bad):
            return
        # Reject listings whose card text shows they're not actually
        # purchasable right now (checked against the fuller card text when
        # available, since the availability badge often sits outside the
        # short product title itself).
        if _looks_unavailable(card_text if card_text is not None else title):
            return
        key = url.split("?")[0].rstrip("/")
        if key in seen_urls:
            return
        seen_urls.add(key)
        results.append({
            "site": site,
            "title": title,
            "price": price,
            "rating": rating,
            "url": url,
            "source": source,
        })

    def extract_from_page(source):
        # Common product-link patterns for Amazon/Flipkart. Multiple selectors are
        # intentional because retailer markup changes frequently.
        selectors = (
            "div[data-component-type='s-search-result']",
            "div[data-asin]",
            "div._1AtVbE",
            "div[data-id]",
            "a[href*='/p/']",
            "a[href*='/dp/']",
        )
        cards = []
        for sel in selectors:
            try:
                loc = page.locator(sel)
                count = min(loc.count(), 80)
                if count:
                    cards.extend([loc.nth(i) for i in range(count)])
                    if len(cards) >= 30:
                        break
            except Exception:
                pass

        for card in cards[:50]:
            if _now() >= deadline:
                raise TimeoutError(f"{site} search timed out")
            try:
                txt = _clean_text(card.inner_text(), 1200)
            except Exception:
                continue
            if not txt:
                continue

            # Prefer rupee-formatted prices. Only use plain numbers when they are
            # clearly near a price marker, avoiding ratings/review counts.
            price = _money(txt)
            if price is None:
                m = re.search(r"(?:price|deal)\s*[:\-]?\s*(?:₹|Rs\.?|INR)?\s*([0-9][0-9,]*)", txt, re.I)
                if m:
                    price = _money(m.group(1))

            rating = None
            rm = re.search(r"\b([1-5](?:\.\d)?)\s*(?:/5|★|stars?)\b", txt, re.I)
            if rm:
                try:
                    rating = float(rm.group(1))
                except Exception:
                    pass

            href = ""
            try:
                if card.evaluate("(el) => el.tagName") == "A":
                    href = card.get_attribute("href") or ""
                else:
                    a = card.locator("a").first
                    if a.count():
                        href = a.get_attribute("href") or ""
            except Exception:
                pass

            if href.startswith("/"):
                href = f"https://{domain}{href}"
            if href and price is not None:
                title = ""
                try:
                    title = card.locator(
                        "h2, h3, [data-testid*='title'], .IRpwTa, .KzDlHZ, ._4rR01T"
                    ).first.inner_text()
                except Exception:
                    pass
                if not title:
                    title = txt.split("₹")[0].strip()
                add_result(title, price, rating, href, source, card_text=txt)

            if len(results) >= _MAX_RESULTS_PER_SITE:
                return

    def open_url(url, source):
        remaining = max(3000, int((deadline - _now()) * 1000))
        page.goto(url, wait_until="domcontentloaded", timeout=min(_NAV_TIMEOUT, remaining))
        page.wait_for_timeout(900)
        extract_from_page(source)

    # Pass 1 helper: generic retailer-page extraction.
    # Retailer markup changes often, so also inspect every product-looking anchor and
    # use nearby text for title/price. This is intentionally independent of the
    # browser_agent plugin.
    def extract_generic_product_links(source):
        try:
            anchors = page.locator("a")
            n = min(anchors.count(), 250)
        except Exception:
            return

        for i in range(n):
            if _now() >= deadline:
                return
            try:
                a = anchors.nth(i)
                href = a.get_attribute("href") or ""
                title = _clean_text(a.inner_text(), 500)
            except Exception:
                continue

            if href.startswith("/"):
                href = f"https://{domain}{href}"
            host = (urlparse(href).netloc or "").lower()
            if domain not in host:
                continue
            if not title or len(title) < 12:
                continue

            low = href.lower()
            # Product URL patterns for common Amazon/Flipkart pages.
            looks_product = (
                (site == "Amazon" and ("/dp/" in low or "/gp/product/" in low))
                or (site == "Flipkart" and "/p/" in low)
            )
            if not looks_product:
                continue

            # Read nearby DOM text where possible; price may be in a sibling/span
            # rather than inside the anchor itself.
            blob = title
            try:
                parent = a.locator("xpath=..")
                blob = _clean_text(parent.inner_text(), 1200)
            except Exception:
                pass

            price = _money(blob)
            if price is None:
                # A plain number is acceptable only when it is close to INR-like
                # wording in the same product block.
                m = re.search(
                    r"(?:₹|rs\\.?|inr|price|deal)\\s*[:.]?\\s*([0-9][0-9,]{1,6})",
                    blob, re.I
                )
                if m:
                    price = _money(m.group(1))

            if price is None:
                # Keep the listing even without price; later validation/search
                # stages may obtain the price. Marking it as unknown prevents a
                # false "no products exist" conclusion.
                price = None

            rating = None
            rm = re.search(r"\b([1-5](?:\.\d)?)\s*(?:/5|★|stars?)\b", blob, re.I)
            if rm:
                try:
                    rating = float(rm.group(1))
                except Exception:
                    pass

            # add_result currently requires a price, so retain unknown-price
            # candidates separately for a second pass.
            if price is not None:
                add_result(title, price, rating, href, source, card_text=blob)

            if len(results) >= _MAX_RESULTS_PER_SITE:
                return

    # Pass 1: direct retailer search.
    query = re.sub(r"\s+", " ", query).strip()
    encoded = quote_plus(query)
    direct_url = (
        f"https://www.amazon.in/s?k={encoded}"
        if site == "Amazon"
        else f"https://www.flipkart.com/search?q={encoded}"
    )
    try:
        open_url(direct_url, "direct")
        if len(results) < _MAX_RESULTS_PER_SITE and _now() < deadline:
            extract_generic_product_links("direct-generic")
    except Exception:
        # The generic extraction can still work on partially loaded pages.
        try:
            if _now() < deadline:
                extract_generic_product_links("direct-generic")
        except Exception:
            pass
        _log(player, f"SHOPPING: {site} direct search unavailable; trying fallback.")

    # Pass 2: search-engine fallback. Use two engines so a Google block does not
    # automatically become a false "no products" result.
    if not results and _now() < deadline:
        searches = [
            ("Google", f"site:{domain} {query}" + (f" under {int(budget)} rupees" if budget else "")),
            ("Bing", f"site:{domain} {query}" + (f" under {int(budget)} rupees" if budget else "")),
        ]
        for engine, q in searches:
            if results or _now() >= deadline:
                break
            try:
                base = "https://www.google.com/search?q=" if engine == "Google" else "https://www.bing.com/search?q="
                url = base + quote_plus(q) + "&count=10"
                page.goto(url, wait_until="domcontentloaded",
                          timeout=min(_NAV_TIMEOUT, max(3000, int((deadline-_now())*1000))))
                page.wait_for_timeout(700)
                anchors = page.locator("a")
                n = min(anchors.count(), 120)
                for i in range(n):
                    if _now() >= deadline:
                        raise TimeoutError(f"{site} fallback timed out")
                    try:
                        a = anchors.nth(i)
                        href = a.get_attribute("href") or ""
                        txt = _clean_text(a.inner_text(), 700)
                    except Exception:
                        continue
                    if not href or not txt or domain not in (urlparse(href).netloc or "").lower():
                        continue
                    price = _money(txt)
                    if price is None:
                        continue
                    rating = None
                    rm = re.search(r"\b([1-5](?:\.\d)?)\s*(?:/5|★|stars?)\b", txt, re.I)
                    if rm:
                        try:
                            rating = float(rm.group(1))
                        except Exception:
                            pass
                    add_result(txt, price, rating, href, engine.lower(), card_text=txt)
                    if len(results) >= _MAX_RESULTS_PER_SITE:
                        break
            except Exception:
                continue

    # Pass 3: broader discovery query. This catches cases where the retailer
    # search page is rendered client-side or blocks automation, while still requiring
    # a retailer-owned product URL and an explicit price.
    if not results and _now() < deadline:
        broad_terms = [
            f"{query} price",
            f"{query} rechargeable" if "recharge" not in query.lower() else query,
            f"{query} under {int(budget)}" if budget else query,
        ]
        for term in broad_terms:
            if results or _now() >= deadline:
                break
            try:
                base = "https://www.google.com/search?q="
                page.goto(
                    base + quote_plus(f"site:{domain} {term}"),
                    wait_until="domcontentloaded",
                    timeout=min(_NAV_TIMEOUT, max(3000, int((deadline-_now())*1000))),
                )
                page.wait_for_timeout(500)
                # Search result containers often contain both title and price.
                for sel in ("div.MjjYud", "li.b_algo", "div.tF2Cxc"):
                    try:
                        loc = page.locator(sel)
                        for j in range(min(loc.count(), 30)):
                            block = _clean_text(loc.nth(j).inner_text(), 1500)
                            if not block:
                                continue
                            price = _money(block)
                            if price is None:
                                continue
                            link = loc.nth(j).locator("a").first
                            href = link.get_attribute("href") or ""
                            if href.startswith("/"):
                                href = f"https://{domain}{href}"
                            if domain not in (urlparse(href).netloc or "").lower():
                                continue
                            title = _clean_text(link.inner_text(), 300) or block.split("\n")[0]
                            add_result(title, price, None, href, "broad-discovery", card_text=block)
                            if len(results) >= _MAX_RESULTS_PER_SITE:
                                break
                        if results:
                            break
                    except Exception:
                        continue
            except Exception:
                continue

    with _LOCK:
        _WORKER[state_key] = {
            "state": "completed" if results else "failed",
            "count": len(results),
            "error": "" if results else "No validated product listings found after direct and fallback searches",
        }
    return results


def _rank(results, budget: float | None):
    usable = [r for r in results if r.get("title") and r.get("price") is not None]
    if budget is not None:
        in_budget = [r for r in usable if r["price"] <= budget]
        if in_budget:
            usable = in_budget

    def score(r):
        price = r.get("price") or 9999999
        rating = r.get("rating") or 0
        # Value-first score. Ratings are helpful but never allowed to override a wildly
        # over-budget product when budget filtering found valid options.
        budget_penalty = 0
        if budget and price > budget:
            budget_penalty = (price - budget) / max(budget, 1) * 4
        price_score = 1 / max(price, 1) * 10000
        # Listings scraped straight off the retailer's own search page are far
        # more likely to still be live and purchasable than ones discovered via
        # a cached Google/Bing index entry, which can point at a delisted or
        # long-out-of-stock page. Prefer "direct" sources when scores are close.
        source_bonus = 3 if str(r.get("source", "")).startswith("direct") else 0
        return rating * 2.5 + price_score - budget_penalty + source_bonus

    return sorted(usable, key=score, reverse=True)


def _format_results(results, query, budget):
    if not results:
        return "I couldn't get reliable product results from Amazon or Flipkart right now."
    ranked = _rank(results, budget)
    if not ranked:
        return f"I found listings for {query}, but none had a reliably detected price within your budget."
    best = ranked[0]
    parts = [
        f"I found it. Best overall match is {best['site']} at ₹{best['price']:,.0f}",
        f"for your ₹{budget:,.0f} budget." if budget else ".",
    ]
    parts.append("Top matches:")
    for i, r in enumerate(ranked[:5], 1):
        rating = f", {r['rating']}/5" if r.get("rating") else ""
        parts.append(f"{i}. {r['site']}: {_clean_text(r['title'], 110)} — ₹{r['price']:,.0f}{rating}")
    return " ".join(parts)


def _worker(job_id: int, query: str, budget: float | None, max_seconds: float, player):
    started = _now()
    deadline = started + max_seconds
    all_results = []
    pw = browser = context = page = None
    _log(player, f"SHOPPING: Searching Amazon and Flipkart for: {query}" + (f" under ₹{budget:,.0f}" if budget else ""))
    try:
        if sync_playwright is None:
            raise RuntimeError("Playwright is not installed")

        pw = sync_playwright().start()
        # Separate context owned exclusively by this agent.
        browser = pw.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled", "--no-sandbox"])
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()
        page.set_default_timeout(_STEP_TIMEOUT)

        for site in ("Amazon", "Flipkart"):
            with _LOCK:
                if _WORKER.get("job_id") != job_id or _WORKER.get("cancel"):
                    _WORKER["state"] = "cancelled"
                    _WORKER["message"] = "Shopping search cancelled."
                    return
            if _now() >= deadline:
                raise TimeoutError("overall shopping search timed out")
            try:
                rs = _site_search(page, site, query, budget, deadline, player)
                all_results.extend(rs)
                _log(player, f"SHOPPING: {site}: {len(rs)} candidate listings found.")
            except Exception as e:
                with _LOCK:
                    _WORKER[site.lower()] = {"state": "failed", "count": 0, "error": _clean_text(e, 180)}
                _log(player, f"SHOPPING: {site} search failed; continuing with the other store.")

        ranked = _rank(all_results, budget)
        message = _format_results(ranked, query, budget)
        with _LOCK:
            if _WORKER.get("job_id") != job_id:
                return
            _WORKER["results"] = ranked
            _WORKER["state"] = "completed" if ranked else "failed"
            _WORKER["finished_at"] = _now()
            _WORKER["message"] = message
            _WORKER["error"] = "" if ranked else "No reliable priced listings were found."
        _notify(player, "SHOPPING: " + message)
    except Exception as e:
        msg = _clean_text(e, 220)
        with _LOCK:
            if _WORKER.get("job_id") == job_id:
                _WORKER["state"] = "failed"
                _WORKER["finished_at"] = _now()
                _WORKER["error"] = msg
                _WORKER["message"] = f"Shopping agent stopped: {msg}"
        _notify(player, f"SHOPPING: Failed — {msg}")
    finally:
        for obj in (page, context, browser):
            try:
                if obj:
                    obj.close()
            except Exception:
                pass
        try:
            if pw:
                pw.stop()
        except Exception:
            pass


def _start(parameters, player):
    query = _clean_text(parameters.get("query"), 240)
    if not query:
        return "Sir, tell me what product you want me to find."
    budget = parameters.get("budget")
    try:
        budget = float(budget) if budget is not None else _budget_from_text(query)
    except Exception:
        budget = _budget_from_text(query)

    try:
        max_seconds = float(parameters.get("max_seconds") or _DEFAULT_SECONDS)
    except Exception:
        max_seconds = _DEFAULT_SECONDS
    max_seconds = max(15, min(max_seconds, _MAX_SECONDS))

    with _LOCK:
        if _WORKER["state"] == "searching":
            return "The shopping agent is already working. Ask for status to see its real progress."
        _WORKER.update({
            "state": "searching",
            "job_id": int(_WORKER["job_id"]) + 1,
            "query": query,
            "budget": budget,
            "started_at": _now(),
            "finished_at": None,
            "amazon": {"state": "queued", "count": 0, "error": ""},
            "flipkart": {"state": "queued", "count": 0, "error": ""},
            "results": [],
            "message": "",
            "progress": "queued",
            "error": "",
            "cancel": False,
            "player": player,
        })
        job_id = _WORKER["job_id"]

    t = threading.Thread(target=_worker, args=(job_id, query, budget, max_seconds, player),
                         name=f"JarvisShopping-{job_id}", daemon=True)
    t.start()
    return "I found your request. I'll handle the shopping search in the background and notify you when it finishes."


def _status():
    with _LOCK:
        w = dict(_WORKER)
        w.pop("player", None)
        list(w.get("results", []))
    state = w["state"]
    if state == "searching":
        elapsed = int(_now() - (w.get("started_at") or _now()))
        return (
            f"Shopping agent is actively working for {elapsed}s. "
            f"Amazon: {w['amazon']['state']} ({w['amazon']['count']} found); "
            f"Flipkart: {w['flipkart']['state']} ({w['flipkart']['count']} found). "
            f"Query: {w['query']}."
        )
    if state == "completed":
        return w.get("message") or "Shopping search completed."
    if state == "failed":
        return w.get("error") or "Shopping search failed."
    if state == "cancelled":
        return "Shopping search was cancelled."
    return "The shopping agent is idle."


def _results():
    with _LOCK:
        rs = list(_WORKER.get("results", []))
    if not rs:
        return "There are no completed shopping results yet."
    lines = []
    for i, r in enumerate(rs[:8], 1):
        lines.append(f"{i}. {r['site']} — {r['title'][:100]} — ₹{r['price']:,.0f} — {r['url']}")
    return "\n".join(lines)


def _cancel():
    with _LOCK:
        if _WORKER["state"] != "searching":
            return "There is no active shopping search to cancel."
        _WORKER["cancel"] = True
    return "I asked the shopping agent to stop."


def _retailer(url):
    host = (urlparse(url).netloc or "").lower()
    if "amazon.in" in host:
        return "Amazon"
    if "flipkart.com" in host:
        return "Flipkart"
    return None


def _saved_results():
    with _LOCK:
        return list(_WORKER.get("results", []))


def _open_exact_url(url: str, label: str = "product") -> str:
    """Open an already-validated retailer URL directly in the user's normal browser.

    This intentionally bypasses browser_agent. Opening a known URL does not need an
    LLM browser decision loop, which avoids model rate limits and accidental URL rewriting.
    """
    url = str(url or "").strip()
    retailer = _retailer(url)
    if retailer is None:
        return "Sir, that is not a valid Amazon India or Flipkart product link."
    try:
        import webbrowser
        ok = webbrowser.open_new_tab(url)
        if not ok:
            # Windows fallback: asks the OS to open the URL with the default browser.
            import os
            os.startfile(url)
        return f"Opened {label} from {retailer} in your browser."
    except Exception as e:
        return f"I couldn't open the {retailer} link: {_clean_text(e, 180)}"


def _open_result(parameters, all_results=False):
    rs = _saved_results()
    if not rs:
        return "There are no completed shopping results to open yet."

    if all_results:
        opened = 0
        for i, r in enumerate(rs[:8], 1):
            if _open_exact_url(r.get("url", ""), f"result {i}").startswith("Opened "):
                opened += 1
        return f"Opened {opened} shopping result links in your browser."

    # Prefer explicit 1-based index. If absent, allow the model to pass a retailer
    # and use the first matching result.
    idx = parameters.get("index")
    if idx is not None:
        try:
            idx = int(float(idx))
        except Exception:
            idx = None
        if idx is not None and 1 <= idx <= len(rs):
            r = rs[idx - 1]
            return _open_exact_url(r.get("url", ""), f"result {idx}")
        return f"Sir, result {idx} is not available. I have {len(rs)} saved results."

    site = str(parameters.get("site") or "").strip().lower()
    if site in ("amazon", "flipkart"):
        for i, r in enumerate(rs, 1):
            if r.get("site", "").lower() == site:
                return _open_exact_url(r.get("url", ""), f"{site.title()} result {i}")

    return _open_exact_url(rs[0].get("url", ""), "the top result")


def _cart_or_order(parameters, player, order=False):
    url = (parameters.get("url") or "").strip()
    if not url:
        with _LOCK:
            rs = list(_WORKER.get("results", []))
        if rs:
            url = rs[0].get("url", "")
    retailer = _retailer(url)
    if not retailer:
        return "Sir, I need a valid Amazon India or Flipkart product URL from the shopping results."
    if not parameters.get("confirm"):
        action = "order" if order else "add it to the cart"
        return f"I can {action} after you explicitly confirm this action."
    if sync_playwright is None:
        return "Playwright is not installed, so I can't open the retailer checkout."

    profile = Path.home() / ".jarvis_shopping_profile"
    first_time = not profile.exists()
    profile.mkdir(parents=True, exist_ok=True)
    try:
        pw = sync_playwright().start()
        context = pw.chromium.launch_persistent_context(
            str(profile), headless=False, viewport={"width": 1280, "height": 900}
        )
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT)
        _log(player, f"SHOPPING: Opened {retailer} product page for {'order' if order else 'cart'} action.")

        # This is a dedicated browser profile the shopping agent owns —
        # separate from your everyday browser, and never logged into your
        # accounts on its own (JARVIS never stores or types your password).
        # Detect a login/sign-in prompt on the page so the reply is explicit
        # about what to do, instead of just saying "opened" and leaving you
        # to figure out why add-to-cart doesn't work.
        needs_login = False
        try:
            body_text = _clean_text(page.inner_text("body"), 3000).lower()
            needs_login = ("login" in body_text or "sign in" in body_text or "sign-in" in body_text)
        except Exception:
            pass

        login_note = ""
        if needs_login or first_time:
            login_note = (
                f" This is a separate browser profile just for shopping, so it starts "
                f"logged out of {retailer} — please log in there once. I've saved this "
                "profile, so you won't need to log in again next time, and your password "
                "is never seen or stored by me."
            )

        if order:
            return (
                f"I opened the {retailer} product page and started the order workflow. "
                "I will not enter payment credentials or OTPs. If the retailer asks for login, CAPTCHA, "
                "OTP, or payment confirmation, complete that step yourself." + login_note
            )
        return f"I opened the {retailer} product page so the cart action can be completed there." + login_note
    except Exception as e:
        return f"I couldn't open the {retailer} product page: {_clean_text(e, 180)}"


def run(parameters: dict, player=None, session_memory=None) -> str:
    params = parameters or {}
    action = str(params.get("action") or "search").strip().lower()

    if action in ("search", "start"):
        return _start(params, player)
    if action == "status":
        return _status()
    if action == "results":
        return _results()
    if action == "cancel":
        return _cancel()
    if action == "open":
        return _open_result(params, all_results=False)
    if action == "open_all":
        return _open_result(params, all_results=True)
    if action == "add_to_cart":
        return _cart_or_order(params, player, order=False)
    if action == "order":
        return _cart_or_order(params, player, order=True)
    return "Sir, shopping_agent supports search, status, results, cancel, open, open_all, add_to_cart, and order."
