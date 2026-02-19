#!/usr/bin/env python3
"""Generate municipality JSON files for major Kansai cities."""
import json
from pathlib import Path

BASE = Path(__file__).parent.parent / "data" / "municipalities"

CITIES = {
    "osaka": [
        ("sakai", "堺市", "さかいし", "Sakai", "https://www.city.sakai.lg.jp"),
        ("higashiosaka", "東大阪市", "ひがしおおさかし", "Higashiosaka", "https://www.city.higashiosaka.lg.jp"),
        ("toyonaka", "豊中市", "とよなかし", "Toyonaka", "https://www.city.toyonaka.osaka.jp"),
        ("suita", "吹田市", "すいたし", "Suita", "https://www.city.suita.osaka.jp"),
        ("takatsuki", "高槻市", "たかつきし", "Takatsuki", "https://www.city.takatsuki.osaka.jp"),
        ("ibaraki-osaka", "茨木市", "いばらきし", "Ibaraki", "https://www.city.ibaraki.osaka.jp"),
        ("hirakata", "枚方市", "ひらかたし", "Hirakata", "https://www.city.hirakata.osaka.jp"),
        ("neyagawa", "寝屋川市", "ねやがわし", "Neyagawa", "https://www.city.neyagawa.osaka.jp"),
        ("yao", "八尾市", "やおし", "Yao", "https://www.city.yao.osaka.jp"),
        ("kishiwada", "岸和田市", "きしわだし", "Kishiwada", "https://www.city.kishiwada.lg.jp"),
    ],
    "kyoto": [
        ("uji", "宇治市", "うじし", "Uji", "https://www.city.uji.kyoto.jp"),
        ("nagaokakyo", "長岡京市", "ながおかきょうし", "Nagaokakyo", "https://www.city.nagaokakyo.lg.jp"),
        ("kameoka", "亀岡市", "かめおかし", "Kameoka", "https://www.city.kameoka.kyoto.jp"),
        ("joyo", "城陽市", "じょうようし", "Joyo", "https://www.city.joyo.kyoto.jp"),
        ("muko", "向日市", "むこうし", "Muko", "https://www.city.muko.kyoto.jp"),
    ],
    "hyogo": [
        ("amagasaki", "尼崎市", "あまがさきし", "Amagasaki", "https://www.city.amagasaki.hyogo.jp"),
        ("nishinomiya", "西宮市", "にしのみやし", "Nishinomiya", "https://www.city.nishinomiya.lg.jp"),
        ("ashiya", "芦屋市", "あしやし", "Ashiya", "https://www.city.ashiya.lg.jp"),
        ("akashi", "明石市", "あかしし", "Akashi", "https://www.city.akashi.lg.jp"),
        ("himeji", "姫路市", "ひめじし", "Himeji", "https://www.city.himeji.lg.jp"),
        ("kakogawa", "加古川市", "かこがわし", "Kakogawa", "https://www.city.kakogawa.lg.jp"),
        ("takarazuka", "宝塚市", "たからづかし", "Takarazuka", "https://www.city.takarazuka.hyogo.jp"),
        ("itami", "伊丹市", "いたみし", "Itami", "https://www.city.itami.lg.jp"),
    ],
    "nara": [
        ("nara", "奈良市", "ならし", "Nara", "https://www.city.nara.lg.jp"),
        ("kashihara", "橿原市", "かしはらし", "Kashihara", "https://www.city.kashihara.nara.jp"),
        ("ikoma", "生駒市", "いこまし", "Ikoma", "https://www.city.ikoma.lg.jp"),
        ("tenri", "天理市", "てんりし", "Tenri", "https://www.city.tenri.nara.jp"),
        ("yamatokoriyama", "大和郡山市", "やまとこおりやまし", "Yamatokoriyama", "https://www.city.yamatokoriyama.lg.jp"),
    ],
    "shiga": [
        ("otsu", "大津市", "おおつし", "Otsu", "https://www.city.otsu.lg.jp"),
        ("kusatsu", "草津市", "くさつし", "Kusatsu", "https://www.city.kusatsu.shiga.jp"),
        ("moriyama", "守山市", "もりやまし", "Moriyama", "https://www.city.moriyama.lg.jp"),
        ("hikone", "彦根市", "ひこねし", "Hikone", "https://www.city.hikone.lg.jp"),
    ],
    "wakayama": [
        ("wakayama", "和歌山市", "わかやまし", "Wakayama", "https://www.city.wakayama.wakayama.jp"),
        ("tanabe", "田辺市", "たなべし", "Tanabe", "https://www.city.tanabe.lg.jp"),
        ("hashimoto", "橋本市", "はしもとし", "Hashimoto", "https://www.city.hashimoto.lg.jp"),
    ],
}

PREFECTURES_JA = {
    "osaka": ("大阪府", "Osaka"),
    "kyoto": ("京都府", "Kyoto"),
    "hyogo": ("兵庫県", "Hyogo"),
    "nara": ("奈良県", "Nara"),
    "shiga": ("滋賀県", "Shiga"),
    "wakayama": ("和歌山県", "Wakayama"),
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
                "last_updated": "2026-02-18",
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
            "last_updated": "2026-02-18",
        }

        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print(f"  CREATED {path.name}")
        created += 1

print(f"\nDone: {created} files created")
