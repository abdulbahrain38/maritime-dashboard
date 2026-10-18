#!/usr/bin/env python3
"""يقرأ صفحة Windward Daily Intelligence العامة ويكتب windward.json. لا يحتاج مكتبات خارجية.

الاستخدام:
    python fetch_windward.py --out site/windward.json
    python fetch_windward.py --input windward.html --out windward.json   (للاختبار دون إنترنت)

يتوقف برمز خطأ ولا يكتب شيئًا إذا تغيّرت بنية الصفحة أو فشل الجلب.
"""
import argparse, json, os, re, sys, time, urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser

URL = "https://insights.windward.ai/"
SKIP_CLASSES = {"tip-box", "tip-icon"}
VOID = {"img", "br", "hr", "meta", "link", "input"}
REQUIRED_MARKERS = ('class="dmap-panel dmap-in"', 'class="dmap-panel dmap-out"',
                    'id="jam-gulf"', 'id="jam-redsea"', 'id="av-cards"', 'id="av-count-all"')
REGION_WORDS = [
    ("hormuz", ("hormuz", "khasab", "musandam", "kumzar", "limah", "qeshm", "larak")),
    ("oman", ("gulf of oman", "fujairah", "sohar", "muscat", "ash shishah", "dahit", "khor fakkan")),
    ("bab", ("red sea", "bab el", "bab-el", "mandeb", "yemen", "aden", "yanbu", "hudaydah", "mokha", "mukha")),
    ("gulf", ("arabian gulf", "persian gulf", "qatar", "basra", "basrah", "umm qasr", "dubai", "bahrain",
              "kish", "ras laffan", "madinat", "al jazeera", "mina saqr", "umm al quwain", "iraq", "kuwait")),
]


class _Text(HTMLParser):
    """يستخرج النصوص ويتجاهل صناديق الشرح (tooltip) والسكربتات."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in VOID:
            return
        if self.skip:
            self.skip += 1
            return
        classes = set((dict(attrs).get("class") or "").split())
        if tag in ("script", "style") or classes & SKIP_CLASSES:
            self.skip = 1

    def handle_endtag(self, tag):
        if tag not in VOID and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip and data.strip():
            self.out.append(re.sub(r"\s+", " ", data).strip())


def texts(fragment):
    p = _Text()
    p.feed(fragment)
    return p.out


def num(s):
    return int(s.replace(",", ""))


def fetch(url, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "maritime-dashboard/1.0 (personal use; contact via repository)",
                "Accept": "text/html"})
            with urllib.request.urlopen(req, timeout=40) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception as e:  # noqa
            last = e
            time.sleep(4 * (i + 1))
    raise SystemExit(f"فشل جلب Windward بعد {tries} محاولات: {last}")


def parse_date(s, fmt):
    try:
        return datetime.strptime(s, fmt).date()
    except Exception:  # noqa
        return None


def parse_transits(t):
    try:
        i = t.index("<!-- INBOUND (left) -->")
        j = t.index("<!-- OUTBOUND (right) -->")
        k = t.index("<!-- /hz-sub Today's Transits -->")
    except ValueError:
        raise SystemExit("لم أجد قسم العبور في الصفحة: تغيّرت البنية، لن أكتب شيئًا.")
    res = {}
    for key, seg in (("inbound", t[i:j]), ("outbound", t[j:k])):
        c = re.search(r'dmap-count">(\d+)', seg)
        d = re.search(r"Dark: (\d+)", seg)
        sub = re.search(r'dmap-sub">([^<]+)<', seg)
        if not c:
            raise SystemExit(f"لم أجد عدد العبور ({key}).")
        vessels = []
        for chunk in re.split(r'(?=<div class="dmap-vrow")', seg)[1:]:
            chunk = chunk.split("dmap-panel-footer")[0]
            items = texts(chunk)
            if len(items) < 3:
                continue
            risk = items[-1] if items[-1] in ("Low", "Moderate", "Medium", "High", "Dark") else None
            vessels.append({
                "class": items[0],
                "name": items[1],
                "details": items[2].rstrip(" ·").strip(),
                "flag": items[-2] if risk and len(items) >= 5 else None,
                "risk": risk,
                "dark": 'data-transmission="dark"' in chunk[:120] or items[1].lower().startswith("dark "),
            })
        res[key] = {
            "count": int(c.group(1)),
            "dark": int(d.group(1)) if d else sum(v["dark"] for v in vessels),
            "summary": re.sub(r"\s+", " ", sub.group(1).replace("&middot;", "·")).strip() if sub else None,
            "vessels": vessels,
        }
    return res


def parse_gps(t):
    a = t.find('id="jam-gulf"')
    b = t.find('id="jam-redsea"')
    c = t.find("<!-- /jam-redsea -->")
    if min(a, b, c) < 0:
        raise SystemExit("لم أجد قسم GPS: تغيّرت البنية، لن أكتب شيئًا.")
    out = {}
    for key, pane in (("gulf", t[a:b]), ("red_sea", t[b:c])):
        big = re.findall(r'font-size:48px[^"]*">\s*([\d,]+)\s*</div>', pane)
        cum = re.findall(r'font-size:28px[^"]*">\s*([\d,]+)\s*</div>', pane)
        delta = re.search(r"([↑↓])\s*([\d.]+)%\s*vs\. 7-day avg", pane)
        trans = re.search(r"out of .*?font-weight:600;color:var\(--t1\);\">([\d,]+)</span>", pane, re.S)
        asof = re.search(r"Data as of (\d{1,2} \w+ \d{4})", pane)
        if len(big) < 2:
            raise SystemExit(f"لم أجد أرقام GPS ({key}).")
        out[key] = {
            "jammed_vessels": num(big[0]),
            "false_meetings": num(big[1]),
            "transmitting": num(trans.group(1)) if trans else None,
            "change_vs_7d_pct": (-1 if delta.group(1) == "↓" else 1) * float(delta.group(2)) if delta else None,
            "since_feb28_unique_vessels": num(cum[0]) if len(cum) > 0 else None,
            "since_feb28_false_meetings": num(cum[1]) if len(cum) > 1 else None,
            "as_of": (parse_date(asof.group(1), "%d %B %Y").isoformat()
                      if asof and parse_date(asof.group(1), "%d %B %Y") else None),
        }
    return out


def region_of(place):
    p = (place or "").lower()
    for region, words in REGION_WORDS:
        if any(w in p for w in words):
            return region
    return "other"


def parse_attacks(t, as_of):
    def count(i):
        m = re.search(rf'id="av-count-{i}".*?av-num">(\d+)', t, re.S)
        return int(m.group(1)) if m else None
    total, attack, miss = count("all"), count("attack"), count("miss")
    if total is None:
        raise SystemExit("لم أجد عدادات السفن المستهدفة.")
    start = t.index('id="av-cards"')
    cards = re.split(r'(?=<div data-event=")', t[start:])[1:]
    items, ref = [], as_of or datetime.now(timezone.utc).date()
    for card in cards:
        end = card.find("</p>")
        if end < 0:
            continue
        head = card[:end + 4]
        ev = re.match(r'<div data-event="([^"]+)"', head)
        parts = texts(head[head.index(">") + 1:])
        is_new = "NEW" in parts[:2]
        parts = [p for p in parts if p != "NEW"]
        di = next((i for i, p in enumerate(parts) if re.fullmatch(r"\d{1,2} [A-Za-z]{3}( \d{4})?", p)), None)
        if di is None or len(parts) < 4:
            continue
        name, meta = parts[0], parts[1] if di > 1 else None
        tags = parts[2:di]
        k = di + 1
        if k < len(parts) and parts[k] == "·":
            k += 1
        place = parts[k] if k < len(parts) else None
        label = parts[k + 1] if k + 1 < len(parts) - 1 else None
        d = parts[di]
        dt = parse_date(d, "%d %b %Y") or parse_date(f"{d} {ref.year}", "%d %b %Y")
        if dt and dt > ref:
            dt = dt.replace(year=dt.year - 1)
        items.append({
            "name": name,
            "meta": re.sub(r"[\U0001F1E6-\U0001F1FF]", "", meta).strip() if meta else None,
            "tags": tags,
            "date": dt.isoformat() if dt else None,
            "place": place,
            "region": region_of(place),
            "kind": (label or ev.group(1)).lower().replace(" ", "-") if (label or ev) else "attack",
            "event": ev.group(1) if ev else None,
            "text": parts[-1],
            "new": is_new,
        })
    if total and len(items) < min(total, 10):
        raise SystemExit(f"قرأت {len(items)} بطاقة فقط من {total}: قد تكون البنية تغيّرت، لن أكتب شيئًا.")
    return {"total": total, "attack": attack, "near_miss": miss, "items": items}


def build(html):
    for m in REQUIRED_MARKERS:
        if m not in html:
            raise SystemExit(f"علامة غير موجودة في الصفحة: {m}. تغيّرت البنية، لن أكتب شيئًا.")
    asof = re.search(r"Data as of (\d{1,2} \w+ \d{4})", html)
    as_of = parse_date(asof.group(1), "%d %B %Y") if asof else None
    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "source": "Windward Daily Intelligence",
        "source_url": URL,
        "data_as_of": as_of.isoformat() if as_of else None,
        "transits": parse_transits(html),
        "gps": parse_gps(html),
        "attacks": parse_attacks(html, as_of),
    }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="windward.json")
    ap.add_argument("--input", help="ملف HTML محلي بدل الجلب من الإنترنت")
    a = ap.parse_args()
    if a.input:
        with open(a.input, encoding="utf-8", errors="replace") as f:
            html = f.read()
    else:
        html = fetch(URL)
    data = build(html)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    tmp = a.out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, a.out)
    tr = data["transits"]
    print(f"تم: بيانات {data['data_as_of']} | عبور {tr['inbound']['count']}+{tr['outbound']['count']} "
          f"| استهداف {len(data['attacks']['items'])} -> {a.out}")


if __name__ == "__main__":
    main()
