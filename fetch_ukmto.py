#!/usr/bin/env python3
"""يجلب بلاغات UKMTO ويكتب data.json للصفحة. لا يحتاج أي مكتبات خارجية.

الاستخدام:
    python fetch_ukmto.py --out site/data.json
    python fetch_ukmto.py --input sample.json --out data.json   (للاختبار دون إنترنت)

إذا تغيّر شكل البيانات أو فشل الجلب يتوقف السكربت برمز خطأ ولا يكتب شيئًا،
فتبقى آخر نسخة سليمة منشورة.
"""
import argparse, json, os, re, sys, time, urllib.request
from datetime import datetime, timezone, timedelta

API = "https://sccd.royalnavy.mod.uk/api/ukmto/all"
KEEP_DAYS = 120
REQUIRED = ("incidentNumber", "utcDateOfIncident", "incidentTypeName",
            "locationLatitude", "locationLongitude", "otherDetails")

# تصنيف المناطق بالإحداثيات أولًا (اسم المنطقة في المصدر قد يكون غير دقيق)
# كل صندوق: (خط عرض أدنى, أعلى, خط طول أدنى, أعلى) -- تُراجع الحدود عند الحاجة
BOXES = [
    ("hormuz", (25.4, 27.2, 55.8, 57.3)),
    ("oman",   (22.0, 25.4, 56.0, 62.5)),
    ("bab",    (12.0, 30.0, 32.0, 43.8)),   # البحر الأحمر وباب المندب
    ("bab",    (10.5, 15.5, 43.0, 53.5)),   # خليج عدن
    ("gulf",   (23.8, 30.7, 47.5, 55.8)),   # الخليج العربي
]
LABELS = {
    "Strait of Hormuz": "hormuz", "Gulf of Oman": "oman",
    "Bab el Mandeb": "bab", "Northern Red Sea": "bab",
    "Southern Red Sea": "bab", "Gulf of Aden": "bab",
    "Persian Gulf": "gulf", "Northern Arabian Gulf": "gulf",
    "Central Arabian Gulf": "gulf", "Southern Arabian Gulf": "gulf",
}
CATEGORY = {
    "Attack": "attack", "Hijack": "piracy", "Attempted Boarding": "piracy",
    "Illegal Boarding": "piracy", "Kidnap": "piracy",
    "Suspicious Activity": "suspicious", "Advisory": "advisory",
}
PIRACY_WORDS = re.compile(
    r"\b(skiff|small craft|pirat|boarded|armed (?:persons|unauthori[sz]ed)|"
    r"unauthori[sz]ed (?:persons|vessel))", re.I)
LOCATION = re.compile(
    r"\b(\d+\s?NM\s+[A-Za-z\- ]+?\s+of\s+[A-Za-z' \-]+(?:,\s*[A-Za-z' \-]+)?)(?=[.\r\n]|$)",
    re.I)


def parse_dt(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def fetch(url, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "maritime-dashboard/1.0 (personal use)"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa
            last = e
            time.sleep(3 * (i + 1))
    raise SystemExit(f"فشل الجلب بعد {tries} محاولات: {last}")


def region_of(lat, lon, place):
    if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
        for name, (a, b, c, d) in BOXES:
            if a <= lat <= b and c <= lon <= d:
                return name
    return LABELS.get((place or "").strip(), "other")


def build(raw):
    if not isinstance(raw, list) or not raw:
        raise SystemExit("الاستجابة ليست قائمة أو فارغة: تغيّر شكل البيانات، لن أكتب شيئًا.")
    good, bad = [], 0
    for r in raw:
        if isinstance(r, dict) and all(k in r for k in REQUIRED):
            try:
                parse_dt(r["utcDateOfIncident"])
                good.append(r)
            except Exception:  # noqa
                bad += 1
        else:
            bad += 1
    if not good or bad / len(raw) > 0.2:
        raise SystemExit(f"{bad} سجل غير صالح من {len(raw)}: تغيّر شكل البيانات، لن أكتب شيئًا.")

    # آخر نسخة لكل رقم تحذير
    by_num, types = {}, {}
    for r in good:
        n = r["incidentNumber"]
        types.setdefault(n, set()).add(r["incidentTypeName"])
        created = r.get("utcDateCreated") or r["utcDateOfIncident"]
        if n not in by_num or created >= (by_num[n].get("utcDateCreated") or ""):
            by_num[n] = r

    cutoff = datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)
    out = []
    for n, r in by_num.items():
        when = parse_dt(r["utcDateOfIncident"])
        if when < cutoff:
            continue
        created = parse_dt(r.get("utcDateCreated") or r["utcDateOfIncident"])
        text = re.sub(r"[ \t]+", " ", (r.get("otherDetails") or "").replace("\r", "")).strip()
        text = re.sub(r"\n{3,}", "\n\n", text)
        tname = r["incidentTypeName"].strip()
        cat = CATEGORY.get(tname, "other")
        pirate = bool(r.get("vesselUnderPirateControl")) or cat == "piracy" \
            or bool(PIRACY_WORDS.search(text))
        loc = LOCATION.search(text)
        loc_text = None
        if loc:  # نقص ما بعد المكان إذا التصق به باقي الجملة
            loc_text = re.split(r"\s+of\s+(?:a|an|the)\s+", loc.group(1), maxsplit=1)[0].strip()[:90]
        out.append({
            "number": n,
            "date": when.isoformat().replace("+00:00", "Z"),
            "reported": created.isoformat().replace("+00:00", "Z"),
            "late_hours": round(max(0.0, (created - when).total_seconds() / 3600), 1),
            "type": tname,
            "category": cat,
            "level": r.get("incidentTypeLevel"),
            "region": region_of(r["locationLatitude"], r["locationLongitude"], r.get("place")),
            "source_place": r.get("place"),
            "lat": r["locationLatitude"],
            "lon": r["locationLongitude"],
            "location_text": loc_text,
            "vessel_type": (r.get("vesselType") or "").strip() or None,
            "piracy": pirate,
            "pirate_control": bool(r.get("vesselUnderPirateControl")),
            "crew_held": r.get("crewHeld") or 0,
            "reclassified": len(types[n]) > 1 or "reclassified" in text.lower(),
            "text": text,
        })
    out.sort(key=lambda x: x["date"], reverse=True)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "source": "UKMTO",
        "count": len(out),
        "incidents": out,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data.json")
    ap.add_argument("--input", help="ملف JSON محلي بدل الجلب من الإنترنت")
    a = ap.parse_args()
    if a.input:
        with open(a.input, encoding="utf-8") as f:
            raw = json.load(f)
    else:
        raw = fetch(API)
    data = build(raw)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    tmp = a.out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, a.out)
    print(f"تم: {data['count']} حادثًا -> {a.out}")


if __name__ == "__main__":
    main()
