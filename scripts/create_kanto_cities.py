#!/usr/bin/env python3
"""Generate municipality JSON files for major Kanto cities."""
import json
from pathlib import Path

BASE = Path(__file__).parent.parent / "data" / "municipalities"

CITIES = {
    "kanagawa": [
        ("yokosuka", "横須賀市", "よこすかし", "Yokosuka", "https://www.city.yokosuka.kanagawa.jp"),
        ("fujisawa", "藤沢市", "ふじさわし", "Fujisawa", "https://www.city.fujisawa.kanagawa.jp"),
        ("hiratsuka", "平塚市", "ひらつかし", "Hiratsuka", "https://www.city.hiratsuka.kanagawa.jp"),
        ("kamakura", "鎌倉市", "かまくらし", "Kamakura", "https://www.city.kamakura.kanagawa.jp"),
        ("atsugi", "厚木市", "あつぎし", "Atsugi", "https://www.city.atsugi.kanagawa.jp"),
        ("odawara", "小田原市", "おだわらし", "Odawara", "https://www.city.odawara.kanagawa.jp"),
        ("yamato", "大和市", "やまとし", "Yamato", "https://www.city.yamato.lg.jp"),
        ("chigasaki", "茅ヶ崎市", "ちがさきし", "Chigasaki", "https://www.city.chigasaki.kanagawa.jp"),
        ("hadano", "秦野市", "はだのし", "Hadano", "https://www.city.hadano.kanagawa.jp"),
        ("ebina", "海老名市", "えびなし", "Ebina", "https://www.city.ebina.kanagawa.jp"),
        ("zama", "座間市", "ざまし", "Zama", "https://www.city.zama.kanagawa.jp"),
        ("isehara", "伊勢原市", "いせはらし", "Isehara", "https://www.city.isehara.kanagawa.jp"),
    ],
    "saitama": [
        ("kawaguchi", "川口市", "かわぐちし", "Kawaguchi", "https://www.city.kawaguchi.lg.jp"),
        ("kawagoe", "川越市", "かわごえし", "Kawagoe", "https://www.city.kawagoe.saitama.jp"),
        ("tokorozawa", "所沢市", "ところざわし", "Tokorozawa", "https://www.city.tokorozawa.saitama.jp"),
        ("koshigaya", "越谷市", "こしがやし", "Koshigaya", "https://www.city.koshigaya.saitama.jp"),
        ("soka", "草加市", "そうかし", "Soka", "https://www.city.soka.saitama.jp"),
        ("kasukabe", "春日部市", "かすかべし", "Kasukabe", "https://www.city.kasukabe.lg.jp"),
        ("ageo", "上尾市", "あげおし", "Ageo", "https://www.city.ageo.lg.jp"),
        ("kumagaya", "熊谷市", "くまがやし", "Kumagaya", "https://www.city.kumagaya.lg.jp"),
        ("niiza", "新座市", "にいざし", "Niiza", "https://www.city.niiza.lg.jp"),
        ("asaka", "朝霞市", "あさかし", "Asaka", "https://www.city.asaka.lg.jp"),
    ],
    "chiba": [
        ("funabashi", "船橋市", "ふなばしし", "Funabashi", "https://www.city.funabashi.lg.jp"),
        ("kashiwa", "柏市", "かしわし", "Kashiwa", "https://www.city.kashiwa.lg.jp"),
        ("matsudo", "松戸市", "まつどし", "Matsudo", "https://www.city.matsudo.chiba.jp"),
        ("ichikawa", "市川市", "いちかわし", "Ichikawa", "https://www.city.ichikawa.lg.jp"),
        ("narita", "成田市", "なりたし", "Narita", "https://www.city.narita.chiba.jp"),
        ("urayasu", "浦安市", "うらやすし", "Urayasu", "https://www.city.urayasu.lg.jp"),
        ("narashino", "習志野市", "ならしのし", "Narashino", "https://www.city.narashino.lg.jp"),
        ("yachiyo", "八千代市", "やちよし", "Yachiyo", "https://www.city.yachiyo.lg.jp"),
    ],
    "gunma": [
        ("maebashi", "前橋市", "まえばしし", "Maebashi", "https://www.city.maebashi.gunma.jp"),
        ("takasaki", "高崎市", "たかさきし", "Takasaki", "https://www.city.takasaki.gunma.jp"),
        ("isesaki", "伊勢崎市", "いせさきし", "Isesaki", "https://www.city.isesaki.lg.jp"),
        ("ota", "太田市", "おおたし", "Ota", "https://www.city.ota.gunma.jp"),
    ],
    "tochigi": [
        ("utsunomiya", "宇都宮市", "うつのみやし", "Utsunomiya", "https://www.city.utsunomiya.tochigi.jp"),
        ("oyama", "小山市", "おやまし", "Oyama", "https://www.city.oyama.tochigi.jp"),
        ("tochigi-city", "栃木市", "とちぎし", "Tochigi", "https://www.city.tochigi.lg.jp"),
    ],
    "ibaraki": [
        ("mito", "水戸市", "みとし", "Mito", "https://www.city.mito.lg.jp"),
        ("tsukuba", "つくば市", "つくばし", "Tsukuba", "https://www.city.tsukuba.lg.jp"),
        ("hitachi", "日立市", "ひたちし", "Hitachi", "https://www.city.hitachi.lg.jp"),
        ("kashima", "鹿嶋市", "かしまし", "Kashima", "https://www.city.kashima.ibaraki.jp"),
    ],
}

PREFECTURES_JA = {
    "kanagawa": ("神奈川県", "Kanagawa"),
    "saitama": ("埼玉県", "Saitama"),
    "chiba": ("千葉県", "Chiba"),
    "gunma": ("群馬県", "Gunma"),
    "tochigi": ("栃木県", "Tochigi"),
    "ibaraki": ("茨城県", "Ibaraki"),
}

created = 0
for pref, cities in CITIES.items():
    pref_dir = BASE / pref
    pref_dir.mkdir(parents=True, exist_ok=True)
    pref_ja, pref_en = PREFECTURES_JA[pref]

    for key, name_ja, reading, name_en, domain in cities:
        path = pref_dir / f"{key}.json"
        if path.exists():
            print(f"  SKIP {path.name} (exists)")
            continue

        data = {
            "_meta": {
                "version": "0.1.0",
                "last_updated": "2026-02-17",
                "confidence": "draft",
            },
            "municipality_id": f"{pref}/{key}",
            "names": {
                "ja": name_ja,
                "reading": reading,
                "romaji": name_en,
                "en": f"{name_en} City",
            },
            "prefecture": {"ja": pref_ja, "en": pref_en},
            "type": "city",
            "offices": [
                {
                    "name_ja": f"{name_ja}役所",
                    "name_en": f"{name_en} City Hall",
                    "is_main": True,
                    "address_ja": "",
                    "address_en": "",
                    "postal_code": "",
                    "phone": "",
                    "hours": {
                        "weekday": "8:30-17:00",
                        "saturday": None,
                        "sunday": None,
                        "note_en": "Closed weekends and national holidays.",
                    },
                    "english_support": None,
                    "english_support_note": "",
                }
            ],
            "forms_available": {
                "residence_registration": {
                    "has_english_version": None,
                    "pdf_download_url_ja": None,
                    "pdf_download_url_en": None,
                    "online_submission": False,
                    "local_field_additions": [],
                    "local_tips": [],
                    "last_verified": None,
                    "verified_by": None,
                }
            },
            "scraping": {
                "domain": domain,
                "residence": {"index_url": None, "direct_pdfs": []},
                "nhi": {"index_url": None, "direct_pdfs": []},
            },
            "useful_links": {
                "official_website": domain + "/",
                "living_guide_en": None,
                "living_guide_ja": None,
            },
            "contributors": [],
            "last_updated": "2026-02-17",
        }

        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print(f"  CREATED {path.name}")
        created += 1

print(f"\nDone: {created} files created")
