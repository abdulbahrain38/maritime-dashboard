#!/usr/bin/env python3
"""يقرأ موجز أخبار القوات البحرية المشتركة (CMF) ويكتب cmf.json. لا يحتاج مكتبات خارجية.

الاستخدام:
    python fetch_cmf.py --out site/cmf.json
    python fetch_cmf.py --input feed.xml --out cmf.json   (للاختبار دون إنترنت)

يحفظ العنوان والتاريخ والرابط فقط، والتفاصيل تُقرأ من موقع CMF نفسه.
يتوقف برمز خطأ ولا يكتب شيئًا إذا فشل الجلب أو تغيّر شكل الموجز.
"""
import argparse, json, os, re, time, urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

URL = "https://combinedmaritimeforces.com/feed/"
MAX_ITEMS = 15
CTF = re.compile(r"\b(?:CTF|Combined Task Force)\s*-?\s*(15[0-4])\b", re.I)


def fetch(url, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "maritime-dashboard/1.0 (personal use)",
                "Accept": "application/rss+xml, application/xml"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read()
        except Exception as e:  # noqa
            last = e
            time.sleep(3 * (i + 1))
    raise SystemExit(f"فشل جلب موجز CMF بعد {tries} محاولات: {last}")


def build(xml):
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as e:
        raise SystemExit(f"الموجز ليس XML صالحًا: {e}")
    items = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        pub = it.findtext("pubDate")
        if not title or not link.startswith("https://"):
            continue
        try:
            date = parsedate_to_datetime(pub).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        except Exception:  # noqa
            date = None
        cats = [c.text.strip() for c in it.findall("category") if c.text]
        ctf = sorted({f"CTF {m}"
                      for m in CTF.findall(title + " " + " ".join(cats))})
        items.append({"title": title, "link": link, "date": date, "ctf": ctf})
        if len(items) >= MAX_ITEMS:
            break
    if not items:
        raise SystemExit("لم أجد أخبارًا في الموجز: تغيّر الشكل، لن أكتب شيئًا.")
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "source": "Combined Maritime Forces",
        "source_url": "https://combinedmaritimeforces.com/news/all-news/",
        "items": items,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="cmf.json")
    ap.add_argument("--input", help="ملف XML محلي بدل الجلب من الإنترنت")
    a = ap.parse_args()
    if a.input:
        with open(a.input, "rb") as f:
            xml = f.read()
    else:
        xml = fetch(URL)
    data = build(xml)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    tmp = a.out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, a.out)
    print(f"تم: {len(data['items'])} خبرًا من CMF -> {a.out}")


if __name__ == "__main__":
    main()
