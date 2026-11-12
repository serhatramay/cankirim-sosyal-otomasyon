import datetime
import email.utils
import html
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

RSS_URL = os.environ.get("RSS_URL", "https://cankirim.com.tr/rss.xml")
MIN_PUBDATE_UTC = datetime.datetime(2026, 11, 12, 23, 10, 0, tzinfo=datetime.timezone.utc)
MAX_POSTS_PER_RUN = 3
USER_AGENT = "CankirimSosyalBot/1.0 (+https://cankirim.com.tr)"

CATEGORY_TAG_MAP = {
    "spor": "#Spor",
    "gündem": "#Gündem",
    "ekonomi": "#Ekonomi",
    "eğitim": "#Eğitim",
    "yaşam": "#Yaşam",
    "kültür": "#Kültür",
    "sağlık": "#Sağlık",
    "asayiş": "#Asayiş",
    "köşe yazıları": "#KöşeYazısı",
}

THREADS_TOPIC_MAP = {
    "spor": "Çankırı Spor",
    "gündem": "Çankırı Haber",
    "ekonomi": "Çankırı Ekonomi",
    "eğitim": "Çankırı Eğitim",
    "yaşam": "Çankırı Yaşam",
    "kültür": "Çankırı Kültür",
    "sağlık": "Çankırı Sağlık",
    "asayiş": "Çankırı Haber",
    "köşe yazıları": "Çankırı Gündem",
}

URL_REGEX = re.compile(r'https?://cankirim\.com\.tr/[^\s"<>]+', re.IGNORECASE)
SKIP_DEATH_REGEX = re.compile(
    r"\b(cansız\s+beden\w*|ölü\s+bulun\w*|hayatını\s+kaybet\w*|yaşamını\s+yitir\w*|"
    r"vefat\s+et\w*|toprağa\s+veril\w*|cenaze\w*|intihar\w*)\b",
    re.IGNORECASE,
)


def unwrap_url(u: str) -> str:
    if not u:
        return ""
    u = html.unescape(u.strip())
    parsed = urllib.parse.urlsplit(u)
    if parsed.netloc.lower() in ("l.threads.net", "l.threads.com", "l.instagram.com", "l.facebook.com"):
        qs = urllib.parse.parse_qs(parsed.query)
        if qs.get("u"):
            return html.unescape(qs["u"][0].strip())
    return u


def norm_url(u: str) -> str:
    u = unwrap_url(u)
    if not u:
        return ""
    parsed = urllib.parse.urlsplit(u)
    scheme = (parsed.scheme or "https").lower()
    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip("/")
    return f"{scheme}://{netloc}{path}"


def http_json(url: str, method: str = "GET", payload=None, headers=None, timeout: int = 20):
    req_headers = {"User-Agent": USER_AGENT}
    if headers:
        req_headers.update(headers)
    data = None
    if payload is not None:
        if isinstance(payload, (dict, list)):
            data = json.dumps(payload).encode("utf-8")
            req_headers.setdefault("Content-Type", "application/json")
        elif isinstance(payload, bytes):
            data = payload
    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_bytes(url: str, timeout: int = 20) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def fetch_rss_items():
    raw_xml = http_bytes(RSS_URL, timeout=20)
    root = ET.fromstring(raw_xml)
    channel = root.find("channel")
    if channel is None:
        return []
    items = []
    for el in channel.findall("item"):
        title = html.unescape((el.findtext("title") or "").strip())
        link = (el.findtext("link") or "").strip()
        pub_raw = (el.findtext("pubDate") or "").strip()
        category = html.unescape((el.findtext("category") or "").strip())
        description = html.unescape((el.findtext("description") or "").strip())
        if not title or not link or not pub_raw:
            continue
        pub_dt = email.utils.parsedate_to_datetime(pub_raw)
        if pub_dt.tzinfo is None:
            pub_dt = pub_dt.replace(tzinfo=datetime.timezone.utc)
        else:
            pub_dt = pub_dt.astimezone(datetime.timezone.utc)
        if pub_dt < MIN_PUBDATE_UTC:
            continue
        if SKIP_DEATH_REGEX.search(f"{title} {description}"):
            continue
        items.append({
            "title": title,
            "link": link,
            "norm_link": norm_url(link),
            "pub_dt": pub_dt,
            "category": category,
            "description": description,
        })
    items.sort(key=lambda x: x["pub_dt"])
    return items


def get_bluesky_session_and_recent_urls(handle: str, app_password: str):
    sess = http_json(
        "https://bsky.social/xrpc/com.atproto.server.createSession",
        method="POST",
        payload={"identifier": handle, "password": app_password},
    )
    jwt = sess["accessJwt"]
    did = sess["did"]
    feed = http_json(
        f"https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed?actor={urllib.parse.quote(did)}&limit=50"
    )
    urls = set()
    for entry in feed.get("feed", []):
        post = entry.get("post") or {}
        record = post.get("record") or {}
        for src in (
            ((post.get("embed") or {}).get("external") or {}).get("uri"),
            ((record.get("embed") or {}).get("external") or {}).get("uri"),
        ):
            if src:
                urls.add(norm_url(src))
        for facet in record.get("facets") or []:
            for feat in facet.get("features") or []:
                uri = feat.get("uri")
                if uri:
                    urls.add(norm_url(uri))
        for m in URL_REGEX.findall(record.get("text") or ""):
            urls.add(norm_url(m))
    return jwt, did, urls


def get_mastodon_recent_urls(instance: str, token: str):
    base = instance.rstrip("/")
    auth = {"Authorization": f"Bearer {token}"}
    acct_info = http_json(f"{base}/api/v1/accounts/verify_credentials", headers=auth)
    acct_id = acct_info["id"]
    acct = acct_info.get("acct") or acct_info.get("username") or ""
    statuses = http_json(f"{base}/api/v1/accounts/{acct_id}/statuses?limit=40", headers=auth)
    urls = set()
    for st in statuses:
        content = st.get("content") or ""
        for m in URL_REGEX.findall(content):
            urls.add(norm_url(m))
        card_url = (st.get("card") or {}).get("url")
        if card_url:
            urls.add(norm_url(card_url))
    return acct, urls


def get_facebook_recent_urls(page_id: str, token: str):
    info = http_json(
        f"https://graph.facebook.com/v21.0/{urllib.parse.quote(page_id)}?"
        + urllib.parse.urlencode({"fields": "name,username", "access_token": token})
    )
    fb_name = info.get("username") or info.get("name") or page_id
    posts = http_json(
        f"https://graph.facebook.com/v21.0/{urllib.parse.quote(page_id)}/published_posts?"
        + urllib.parse.urlencode({
            "fields": "id,message,attachments{unshimmed_url,url}",
            "limit": "30",
            "access_token": token,
        })
    )
    urls = set()
    for post in posts.get("data") or []:
        for att in (post.get("attachments") or {}).get("data") or []:
            u = att.get("unshimmed_url") or att.get("url")
            if u and "cankirim.com.tr" in u.lower():
                urls.add(norm_url(u))
        for m in URL_REGEX.findall(post.get("message") or ""):
            urls.add(norm_url(m))
    return fb_name, urls


def get_threads_recent_urls(user_id: str, token: str):
    days_left = None
    try:
        dbg = http_json(
            "https://graph.threads.net/debug_token?"
            + urllib.parse.urlencode({"input_token": token, "access_token": token})
        )
        expires_at = (dbg.get("data") or {}).get("expires_at")
        if expires_at:
            days_left = (float(expires_at) - time.time()) / 86400.0
            pat = os.environ.get("GH_SECRET_PAT", "").strip()
            repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
            if 0 < days_left < 45 and pat and repo:
                try:
                    ref = http_json(
                        "https://graph.threads.net/refresh_access_token?"
                        + urllib.parse.urlencode({"grant_type": "th_refresh_token", "access_token": token})
                    )
                    new_tok = (ref.get("access_token") or "").strip()
                    if new_tok:
                        subprocess.run(
                            ["gh", "secret", "set", "THREADS_ACCESS_TOKEN", "-R", repo],
                            input=new_tok.encode("utf-8"),
                            env={**os.environ, "GH_TOKEN": pat},
                            check=True,
                        )
                        token = new_tok
                        days_left = float(ref.get("expires_in", 5184000)) / 86400.0
                        print("Threads token refreshed and saved to THREADS_ACCESS_TOKEN")
                except Exception as exc:
                    print(f"WARN Threads token refresh failed: {exc}")
    except Exception as exc:
        print(f"WARN Threads debug_token check failed: {exc}")

    info = http_json(
        f"https://graph.threads.net/v1.0/{urllib.parse.quote(user_id)}?"
        + urllib.parse.urlencode({"fields": "id,username", "access_token": token})
    )
    th_username = info.get("username") or user_id
    posts = http_json(
        f"https://graph.threads.net/v1.0/{urllib.parse.quote(user_id)}/threads?"
        + urllib.parse.urlencode({
            "fields": "id,text,link_attachment_url,permalink",
            "limit": "35",
            "access_token": token,
        })
    )
    urls = set()
    for post in posts.get("data") or []:
        la = unwrap_url(post.get("link_attachment_url") or "")
        if la and "cankirim.com.tr" in la.lower():
            urls.add(norm_url(la))
        for m in URL_REGEX.findall(post.get("text") or ""):
            urls.add(norm_url(m))
    return th_username, days_left, token, urls


def fetch_article_og(url: str, fallback_desc: str):
    og_desc = fallback_desc
    img_bytes = b""
    try:
        page_html = http_bytes(url, timeout=20).decode("utf-8", "ignore")
        m_desc = (
            re.search(r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)["\']', page_html, re.I)
            or re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:description["\']', page_html, re.I)
        )
        if m_desc:
            og_desc = html.unescape(m_desc.group(1)).strip()
        m_img = (
            re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', page_html, re.I)
            or re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', page_html, re.I)
        )
        if m_img:
            og_img_url = html.unescape(m_img.group(1)).strip()
            img_bytes = http_bytes(og_img_url, timeout=20)
    except Exception as exc:
        print(f"  WARN og fetch failed for {url}: {exc}")
    return og_desc or fallback_desc, img_bytes


def build_hashtags(title: str, category: str):
    tags = ["#Çankırı"]
    cat_key = category.strip().lower()
    if cat_key in CATEGORY_TAG_MAP:
        tags.append(CATEGORY_TAG_MAP[cat_key])
    if "çankırı fk" in title.lower() and "#ÇankırıFK" not in tags:
        tags.append("#ÇankırıFK")
    return tags[:3]


def post_to_bluesky(jwt: str, did: str, item: dict, og_desc: str, img_bytes: bytes, tags: list):
    tags_str = " ".join(tags)
    suffix = f"\n\nDetaylar: cankirim.com.tr\n\n{tags_str}"
    max_title = max(20, 290 - len(suffix))
    title_text = item["title"]
    if len(title_text) > max_title:
        title_text = title_text[: max_title - 1].rstrip() + "…"
    bsky_text = f"{title_text}{suffix}"

    facets = []

    def add_facet(substr: str, feature: dict):
        idx = bsky_text.index(substr)
        b_start = len(bsky_text[:idx].encode("utf-8"))
        b_end = b_start + len(substr.encode("utf-8"))
        facets.append({"index": {"byteStart": b_start, "byteEnd": b_end}, "features": [feature]})

    add_facet("cankirim.com.tr", {"$type": "app.bsky.richtext.facet#link", "uri": item["link"]})
    for tag in tags:
        add_facet(tag, {"$type": "app.bsky.richtext.facet#tag", "tag": tag.lstrip("#")})

    external = {
        "uri": item["link"],
        "title": item["title"][:250],
        "description": (og_desc or item["description"])[:400],
    }
    if img_bytes and len(img_bytes) <= 950_000:
        blob_resp = http_json(
            "https://bsky.social/xrpc/com.atproto.repo.uploadBlob",
            method="POST",
            payload=img_bytes,
            headers={"Authorization": f"Bearer {jwt}", "Content-Type": "image/jpeg"},
        )
        if blob_resp.get("blob"):
            external["thumb"] = blob_resp["blob"]

    record = {
        "$type": "app.bsky.feed.post",
        "text": bsky_text,
        "langs": ["tr"],
        "facets": facets,
        "createdAt": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "embed": {
            "$type": "app.bsky.embed.external",
            "external": external,
        },
    }
    res = http_json(
        "https://bsky.social/xrpc/com.atproto.repo.createRecord",
        method="POST",
        payload={"repo": did, "collection": "app.bsky.feed.post", "record": record},
        headers={"Authorization": f"Bearer {jwt}"},
    )
    return res.get("uri", "")


def post_to_mastodon(instance: str, token: str, item: dict, og_desc: str, tags: list):
    base = instance.rstrip("/")
    tags_str = " ".join(tags)
    desc = (og_desc or item["description"] or "").strip()
    head = item["title"].strip()
    tail = f"\n\n{item['link']}\n\n{tags_str}"
    avail = 485 - len(head) - len(tail) - 2
    if desc and avail > 30:
        if len(desc) > avail:
            desc = desc[: avail - 1].rstrip() + "…"
        status_text = f"{head}\n\n{desc}{tail}"
    else:
        max_h = max(20, 485 - len(tail))
        if len(head) > max_h:
            head = head[: max_h - 1].rstrip() + "…"
        status_text = f"{head}{tail}"

    res = http_json(
        f"{base}/api/v1/statuses",
        method="POST",
        payload={"status": status_text, "visibility": "public", "language": "tr"},
        headers={"Authorization": f"Bearer {token}"},
    )
    return res.get("url", "")


def post_to_facebook(page_id: str, token: str, item: dict, og_desc: str, tags: list):
    desc = (og_desc or item["description"] or "").strip()
    tags_str = " ".join(tags)
    title = item["title"].strip()
    message = f"{title}\n\n{desc}\n\n{tags_str}" if desc else f"{title}\n\n{tags_str}"
    body = urllib.parse.urlencode({
        "message": message,
        "link": item["link"],
        "access_token": token,
    }).encode("utf-8")
    res = http_json(
        f"https://graph.facebook.com/v21.0/{urllib.parse.quote(page_id)}/feed",
        method="POST",
        payload=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=20,
    )
    return res.get("id", "")


def post_to_threads(user_id: str, token: str, item: dict, og_desc: str):
    head = item["title"].strip()
    desc = (og_desc or item["description"] or "").strip()
    if desc and len(head) + 2 < 490:
        avail = 490 - len(head) - 2
        if avail > 30:
            if len(desc) > avail:
                desc = desc[: avail - 1].rstrip() + "…"
            text = f"{head}\n\n{desc}"
        else:
            text = head[:490]
    else:
        text = head[:489].rstrip() + "…" if len(head) > 490 else head

    topic_tag = (
        "Çankırı FK"
        if "çankırı fk" in item["title"].lower()
        else THREADS_TOPIC_MAP.get(item["category"].strip().lower(), "Çankırı Haber")
    )
    fields = {
        "media_type": "TEXT",
        "text": text,
        "link_attachment": item["link"],
        "topic_tag": topic_tag,
        "access_token": token,
    }
    uid_q = urllib.parse.quote(user_id)
    try:
        c_res = http_json(
            f"https://graph.threads.net/v1.0/{uid_q}/threads",
            method="POST",
            payload=urllib.parse.urlencode(fields).encode("utf-8"),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=20,
        )
    except urllib.error.HTTPError as err:
        if err.code == 400:
            fields.pop("topic_tag", None)
            c_res = http_json(
                f"https://graph.threads.net/v1.0/{uid_q}/threads",
                method="POST",
                payload=urllib.parse.urlencode(fields).encode("utf-8"),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=20,
            )
        else:
            raise

    creation_id = c_res["id"]
    for _ in range(12):
        st = http_json(
            f"https://graph.threads.net/v1.0/{urllib.parse.quote(creation_id)}?"
            + urllib.parse.urlencode({"fields": "status,error_message", "access_token": token}),
            timeout=20,
        )
        status = st.get("status")
        if status == "FINISHED":
            break
        if status in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Threads container {status}: {st.get('error_message', st)}")
        time.sleep(2)

    pub = http_json(
        f"https://graph.threads.net/v1.0/{uid_q}/threads_publish",
        method="POST",
        payload=urllib.parse.urlencode({"creation_id": creation_id, "access_token": token}).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=20,
    )
    post_id = pub["id"]
    detail = http_json(
        f"https://graph.threads.net/v1.0/{urllib.parse.quote(post_id)}?"
        + urllib.parse.urlencode({"fields": "id,permalink", "access_token": token}),
        timeout=20,
    )
    return detail.get("permalink") or post_id


def main():
    dry_run = "--dry-run" in sys.argv[1:] or os.environ.get("DRY_RUN", "").lower() == "true"
    print(f"RSS Social Sync started (dry_run={dry_run})")

    items = fetch_rss_items()
    print(f"RSS parsed: {len(items)} eligible items >= {MIN_PUBDATE_UTC.isoformat()}")

    bsky_handle = os.environ.get("BLUESKY_HANDLE", "").strip()
    bsky_pass = os.environ.get("BLUESKY_APP_PASSWORD", "").strip()
    masto_instance = os.environ.get("MASTODON_INSTANCE", "https://mastodon.social").strip()
    masto_token = os.environ.get("MASTODON_ACCESS_TOKEN", "").strip()
    fb_page_id = os.environ.get("FACEBOOK_PAGE_ID", "").strip()
    fb_token = os.environ.get("FACEBOOK_PAGE_ACCESS_TOKEN", "").strip()
    threads_user_id = os.environ.get("THREADS_USER_ID", "").strip()
    threads_token = os.environ.get("THREADS_ACCESS_TOKEN", "").strip()

    bsky_jwt = bsky_did = None
    bsky_urls = set()
    if bsky_handle and bsky_pass:
        bsky_jwt, bsky_did, bsky_urls = get_bluesky_session_and_recent_urls(bsky_handle, bsky_pass)
        print(f"Bluesky connected ({bsky_handle}, did={bsky_did}): {len(bsky_urls)} existing URLs detected")
    else:
        print("Bluesky skipped: missing credentials")

    masto_acct = None
    masto_urls = set()
    if masto_token:
        masto_acct, masto_urls = get_mastodon_recent_urls(masto_instance, masto_token)
        print(f"Mastodon connected (@{masto_acct}): {len(masto_urls)} existing URLs detected")
    else:
        print("Mastodon skipped: missing credentials")

    fb_ready = False
    fb_urls = set()
    if fb_page_id and fb_token:
        try:
            fb_name, fb_urls = get_facebook_recent_urls(fb_page_id, fb_token)
            fb_ready = True
            print(f"Facebook connected (@{fb_name}): {len(fb_urls)} existing URLs detected")
        except Exception as exc:
            print(f"Facebook connect ERROR: {exc}")
    else:
        print("Facebook skipped: missing credentials")

    threads_ready = False
    threads_urls = set()
    if threads_user_id and threads_token:
        try:
            th_name, th_days, threads_token, threads_urls = get_threads_recent_urls(threads_user_id, threads_token)
            threads_ready = True
            days_note = f", {th_days:.1f}d left" if th_days is not None else ""
            print(f"Threads connected (@{th_name}{days_note}): {len(threads_urls)} existing URLs detected")
        except Exception as exc:
            print(f"Threads connect ERROR: {exc}")
    else:
        print("Threads skipped: missing credentials")

    if not items:
        print("No eligible RSS items found.")
        return 0

    posted_count = 0
    total_failures = 0

    for item in items:
        if posted_count >= MAX_POSTS_PER_RUN:
            print(f"Reached MAX_POSTS_PER_RUN={MAX_POSTS_PER_RUN}; remaining queued for next run.")
            break

        need_bsky = bool(bsky_jwt) and item["norm_link"] not in bsky_urls
        need_masto = bool(masto_token) and item["norm_link"] not in masto_urls
        need_fb = fb_ready and item["norm_link"] not in fb_urls
        need_threads = threads_ready and item["norm_link"] not in threads_urls
        if not need_bsky and not need_masto and not need_fb and not need_threads:
            continue

        tags = build_hashtags(item["title"], item["category"])
        print(
            f"Pending item: {item['title']} | bsky={need_bsky} masto={need_masto} "
            f"fb={need_fb} threads={need_threads} | tags={tags}"
        )

        if dry_run:
            posted_count += 1
            continue

        og_desc, img_bytes = fetch_article_og(item["link"], item["description"])
        attempts_for_item = 0
        successes_for_item = 0

        if need_bsky:
            attempts_for_item += 1
            try:
                uri = post_to_bluesky(bsky_jwt, bsky_did, item, og_desc, img_bytes, tags)
                print(f"  Bluesky OK: {uri}")
                bsky_urls.add(item["norm_link"])
                successes_for_item += 1
            except Exception as exc:
                print(f"  Bluesky ERROR: {exc}")

        if need_masto:
            attempts_for_item += 1
            try:
                url = post_to_mastodon(masto_instance, masto_token, item, og_desc, tags)
                print(f"  Mastodon OK: {url}")
                masto_urls.add(item["norm_link"])
                successes_for_item += 1
            except Exception as exc:
                print(f"  Mastodon ERROR: {exc}")

        if need_fb:
            attempts_for_item += 1
            try:
                fb_post_id = post_to_facebook(fb_page_id, fb_token, item, og_desc, tags)
                print(f"  Facebook OK: {fb_post_id}")
                fb_urls.add(item["norm_link"])
                successes_for_item += 1
            except Exception as exc:
                print(f"  Facebook ERROR: {exc}")

        if need_threads:
            attempts_for_item += 1
            try:
                th_link = post_to_threads(threads_user_id, threads_token, item, og_desc)
                print(f"  Threads OK: {th_link}")
                threads_urls.add(item["norm_link"])
                successes_for_item += 1
            except Exception as exc:
                print(f"  Threads ERROR: {exc}")

        if attempts_for_item > 0 and successes_for_item == 0:
            total_failures += 1
        else:
            posted_count += 1

    print(f"Done. processed={posted_count}, all_platform_failures={total_failures}")
    return 1 if total_failures > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
