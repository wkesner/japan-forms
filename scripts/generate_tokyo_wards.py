#!/usr/bin/env python3
"""
Generate municipality JSON files for all 23 Tokyo special wards.
Minato-ku already exists — this creates the remaining 22.
"""

import json
import os

WARDS = [
    {
        "id": "adachi-ku",
        "ja": "足立区", "reading": "あだちく", "romaji": "Adachi-ku", "en": "Adachi Ward",
        "main_office_ja": "足立区役所",
        "main_office_en": "Adachi City Hall",
        "address_ja": "〒120-8510 東京都足立区中央本町1丁目17番1号",
        "address_en": "1-17-1 Chuohoncho, Adachi-ku, Tokyo 120-8510",
        "phone": "03-3880-5111",
        "website": "https://www.city.adachi.tokyo.jp/",
        "english_support": "partial",
        "note": "Large ward in northeast Tokyo. Growing international population."
    },
    {
        "id": "arakawa-ku",
        "ja": "荒川区", "reading": "あらかわく", "romaji": "Arakawa-ku", "en": "Arakawa Ward",
        "main_office_ja": "荒川区役所",
        "main_office_en": "Arakawa City Hall",
        "address_ja": "〒116-8501 東京都荒川区荒川2丁目2番3号",
        "address_en": "2-2-3 Arakawa, Arakawa-ku, Tokyo 116-8501",
        "phone": "03-3802-3111",
        "website": "https://www.city.arakawa.tokyo.jp/",
        "english_support": "partial",
        "note": "Smaller ward along Arakawa River. Traditional shitamachi atmosphere."
    },
    {
        "id": "bunkyo-ku",
        "ja": "文京区", "reading": "ぶんきょうく", "romaji": "Bunkyō-ku", "en": "Bunkyo Ward",
        "main_office_ja": "文京シビックセンター",
        "main_office_en": "Bunkyo Civic Center",
        "address_ja": "〒112-8555 東京都文京区春日1丁目16番21号",
        "address_en": "1-16-21 Kasuga, Bunkyo-ku, Tokyo 112-8555",
        "phone": "03-3812-7111",
        "website": "https://www.city.bunkyo.lg.jp/",
        "english_support": "partial",
        "note": "University district. Many international students and researchers."
    },
    {
        "id": "chiyoda-ku",
        "ja": "千代田区", "reading": "ちよだく", "romaji": "Chiyoda-ku", "en": "Chiyoda Ward",
        "main_office_ja": "千代田区役所",
        "main_office_en": "Chiyoda City Hall",
        "address_ja": "〒102-8688 東京都千代田区九段南1丁目2番1号",
        "address_en": "1-2-1 Kudanminami, Chiyoda-ku, Tokyo 102-8688",
        "phone": "03-3264-2111",
        "website": "https://www.city.chiyoda.lg.jp/",
        "english_support": "partial",
        "note": "Political center of Japan. Imperial Palace, government buildings. Small residential population but many businesses."
    },
    {
        "id": "chuo-ku",
        "ja": "中央区", "reading": "ちゅうおうく", "romaji": "Chūō-ku", "en": "Chuo Ward",
        "main_office_ja": "中央区役所",
        "main_office_en": "Chuo City Hall",
        "address_ja": "〒104-8404 東京都中央区築地1丁目1番1号",
        "address_en": "1-1-1 Tsukiji, Chuo-ku, Tokyo 104-8404",
        "phone": "03-3543-0211",
        "website": "https://www.city.chuo.lg.jp/",
        "english_support": "partial",
        "note": "Ginza, Nihonbashi, Tsukiji area. Business district with growing residential population."
    },
    {
        "id": "edogawa-ku",
        "ja": "江戸川区", "reading": "えどがわく", "romaji": "Edogawa-ku", "en": "Edogawa Ward",
        "main_office_ja": "江戸川区役所",
        "main_office_en": "Edogawa City Hall",
        "address_ja": "〒132-8501 東京都江戸川区中央1丁目4番1号",
        "address_en": "1-4-1 Chuo, Edogawa-ku, Tokyo 132-8501",
        "phone": "03-3652-1151",
        "website": "https://www.city.edogawa.tokyo.jp/",
        "english_support": "partial",
        "note": "Eastern Tokyo. Large Indian community. Near Tokyo Disneyland."
    },
    {
        "id": "itabashi-ku",
        "ja": "板橋区", "reading": "いたばしく", "romaji": "Itabashi-ku", "en": "Itabashi Ward",
        "main_office_ja": "板橋区役所",
        "main_office_en": "Itabashi City Hall",
        "address_ja": "〒173-8501 東京都板橋区板橋2丁目66番1号",
        "address_en": "2-66-1 Itabashi, Itabashi-ku, Tokyo 173-8501",
        "phone": "03-3964-1111",
        "website": "https://www.city.itabashi.tokyo.jp/",
        "english_support": "partial",
        "note": "Northern Tokyo residential ward."
    },
    {
        "id": "katsushika-ku",
        "ja": "葛飾区", "reading": "かつしかく", "romaji": "Katsushika-ku", "en": "Katsushika Ward",
        "main_office_ja": "葛飾区役所",
        "main_office_en": "Katsushika City Hall",
        "address_ja": "〒124-8555 東京都葛飾区立石5丁目13番1号",
        "address_en": "5-13-1 Tateishi, Katsushika-ku, Tokyo 124-8555",
        "phone": "03-3695-1111",
        "website": "https://www.city.katsushika.lg.jp/",
        "english_support": "partial",
        "note": "Eastern Tokyo. Famous for Tora-san films. Traditional working-class area."
    },
    {
        "id": "kita-ku",
        "ja": "北区", "reading": "きたく", "romaji": "Kita-ku", "en": "Kita Ward",
        "main_office_ja": "北区役所",
        "main_office_en": "Kita City Hall",
        "address_ja": "〒114-8508 東京都北区王子本町1丁目15番22号",
        "address_en": "1-15-22 Ojihoncho, Kita-ku, Tokyo 114-8508",
        "phone": "03-3908-1111",
        "website": "https://www.city.kita.tokyo.jp/",
        "english_support": "partial",
        "note": "Northern Tokyo. Affordable residential area with good transit."
    },
    {
        "id": "koto-ku",
        "ja": "江東区", "reading": "こうとうく", "romaji": "Kōtō-ku", "en": "Koto Ward",
        "main_office_ja": "江東区役所",
        "main_office_en": "Koto City Hall",
        "address_ja": "〒135-8383 東京都江東区東陽4丁目11番28号",
        "address_en": "4-11-28 Toyo, Koto-ku, Tokyo 135-8383",
        "phone": "03-3647-9111",
        "website": "https://www.city.koto.lg.jp/",
        "english_support": "partial",
        "note": "Includes Toyosu Market, Odaiba, and growing waterfront residential areas."
    },
    {
        "id": "meguro-ku",
        "ja": "目黒区", "reading": "めぐろく", "romaji": "Meguro-ku", "en": "Meguro Ward",
        "main_office_ja": "目黒区総合庁舎",
        "main_office_en": "Meguro General Government Building",
        "address_ja": "〒153-8573 東京都目黒区上目黒2丁目19番15号",
        "address_en": "2-19-15 Kamimeguro, Meguro-ku, Tokyo 153-8573",
        "phone": "03-3715-1111",
        "website": "https://www.city.meguro.tokyo.jp/",
        "english_support": "partial",
        "note": "Upscale residential area. Nakameguro, Jiyugaoka. Popular with expat families."
    },
    {
        "id": "nakano-ku",
        "ja": "中野区", "reading": "なかのく", "romaji": "Nakano-ku", "en": "Nakano Ward",
        "main_office_ja": "中野区役所",
        "main_office_en": "Nakano City Hall",
        "address_ja": "〒164-8501 東京都中野区中野4丁目8番1号",
        "address_en": "4-8-1 Nakano, Nakano-ku, Tokyo 164-8501",
        "phone": "03-3389-1111",
        "website": "https://www.city.tokyo-nakano.lg.jp/",
        "english_support": "partial",
        "note": "Known for Nakano Broadway (anime/manga). Affordable central location."
    },
    {
        "id": "nerima-ku",
        "ja": "練馬区", "reading": "ねりまく", "romaji": "Nerima-ku", "en": "Nerima Ward",
        "main_office_ja": "練馬区役所",
        "main_office_en": "Nerima City Hall",
        "address_ja": "〒176-8501 東京都練馬区豊玉北6丁目12番1号",
        "address_en": "6-12-1 Toyotamakita, Nerima-ku, Tokyo 176-8501",
        "phone": "03-3993-1111",
        "website": "https://www.city.nerima.tokyo.jp/",
        "english_support": "partial",
        "note": "Birthplace of Japanese animation. Residential, family-friendly."
    },
    {
        "id": "ota-ku",
        "ja": "大田区", "reading": "おおたく", "romaji": "Ōta-ku", "en": "Ota Ward",
        "main_office_ja": "大田区役所",
        "main_office_en": "Ota City Hall",
        "address_ja": "〒144-8621 東京都大田区蒲田5丁目13番14号",
        "address_en": "5-13-14 Kamata, Ota-ku, Tokyo 144-8621",
        "phone": "03-5744-1111",
        "website": "https://www.city.ota.tokyo.jp/",
        "english_support": "partial",
        "note": "Largest Tokyo ward by area. Home to Haneda Airport. Growing international community."
    },
    {
        "id": "setagaya-ku",
        "ja": "世田谷区", "reading": "せたがやく", "romaji": "Setagaya-ku", "en": "Setagaya Ward",
        "main_office_ja": "世田谷区役所",
        "main_office_en": "Setagaya City Hall",
        "address_ja": "〒154-8504 東京都世田谷区世田谷4丁目21番27号",
        "address_en": "4-21-27 Setagaya, Setagaya-ku, Tokyo 154-8504",
        "phone": "03-5432-1111",
        "website": "https://www.city.setagaya.lg.jp/",
        "english_support": "partial",
        "note": "Most populous Tokyo ward. Shimokitazawa, Sangenjaya. Popular with young professionals and families."
    },
    {
        "id": "shibuya-ku",
        "ja": "渋谷区", "reading": "しぶやく", "romaji": "Shibuya-ku", "en": "Shibuya Ward",
        "main_office_ja": "渋谷区役所",
        "main_office_en": "Shibuya City Hall",
        "address_ja": "〒150-8010 東京都渋谷区宇田川町1番1号",
        "address_en": "1-1 Udagawacho, Shibuya-ku, Tokyo 150-8010",
        "phone": "03-3463-1211",
        "website": "https://www.city.shibuya.tokyo.jp/",
        "english_support": "partial",
        "note": "Major commercial/entertainment hub. Large international presence. IT startups concentrated here."
    },
    {
        "id": "shinagawa-ku",
        "ja": "品川区", "reading": "しながわく", "romaji": "Shinagawa-ku", "en": "Shinagawa Ward",
        "main_office_ja": "品川区役所",
        "main_office_en": "Shinagawa City Hall",
        "address_ja": "〒140-8715 東京都品川区広町2丁目1番36号",
        "address_en": "2-1-36 Hiromachi, Shinagawa-ku, Tokyo 140-8715",
        "phone": "03-3777-1111",
        "website": "https://www.city.shinagawa.tokyo.jp/",
        "english_support": "partial",
        "note": "Major transport hub (Shinagawa Station / Shinkansen). Growing waterfront business area."
    },
    {
        "id": "shinjuku-ku",
        "ja": "新宿区", "reading": "しんじゅくく", "romaji": "Shinjuku-ku", "en": "Shinjuku Ward",
        "main_office_ja": "新宿区役所",
        "main_office_en": "Shinjuku City Hall",
        "address_ja": "〒160-8484 東京都新宿区歌舞伎町1丁目4番1号",
        "address_en": "1-4-1 Kabukicho, Shinjuku-ku, Tokyo 160-8484",
        "phone": "03-3209-1111",
        "website": "https://www.city.shinjuku.lg.jp/",
        "english_support": True,
        "note": "Highest foreign resident population of any Tokyo ward. Multilingual services available. Shinjuku Foreign Residents Advisory Corner offers free consultations."
    },
    {
        "id": "suginami-ku",
        "ja": "杉並区", "reading": "すぎなみく", "romaji": "Suginami-ku", "en": "Suginami Ward",
        "main_office_ja": "杉並区役所",
        "main_office_en": "Suginami City Hall",
        "address_ja": "〒166-8570 東京都杉並区阿佐谷南1丁目15番1号",
        "address_en": "1-15-1 Asagayaminami, Suginami-ku, Tokyo 166-8570",
        "phone": "03-3312-2111",
        "website": "https://www.city.suginami.tokyo.jp/",
        "english_support": "partial",
        "note": "Residential ward. Koenji, Asagaya areas. Animation studios."
    },
    {
        "id": "sumida-ku",
        "ja": "墨田区", "reading": "すみだく", "romaji": "Sumida-ku", "en": "Sumida Ward",
        "main_office_ja": "墨田区役所",
        "main_office_en": "Sumida City Hall",
        "address_ja": "〒130-8640 東京都墨田区吾妻橋1丁目23番20号",
        "address_en": "1-23-20 Azumabashi, Sumida-ku, Tokyo 130-8640",
        "phone": "03-5608-1111",
        "website": "https://www.city.sumida.lg.jp/",
        "english_support": "partial",
        "note": "Home to Tokyo Skytree. Traditional shitamachi culture."
    },
    {
        "id": "taito-ku",
        "ja": "台東区", "reading": "たいとうく", "romaji": "Taitō-ku", "en": "Taito Ward",
        "main_office_ja": "台東区役所",
        "main_office_en": "Taito City Hall",
        "address_ja": "〒110-8615 東京都台東区東上野4丁目5番6号",
        "address_en": "4-5-6 Higashiueno, Taito-ku, Tokyo 110-8615",
        "phone": "03-5246-1111",
        "website": "https://www.city.taito.lg.jp/",
        "english_support": "partial",
        "note": "Ueno, Asakusa, Senso-ji. Smallest ward by area. High tourist traffic, significant international resident community."
    },
    {
        "id": "toshima-ku",
        "ja": "豊島区", "reading": "としまく", "romaji": "Toshima-ku", "en": "Toshima Ward",
        "main_office_ja": "豊島区役所",
        "main_office_en": "Toshima City Hall",
        "address_ja": "〒171-8422 東京都豊島区南池袋2丁目45番1号",
        "address_en": "2-45-1 Minamiikebukuro, Toshima-ku, Tokyo 171-8422",
        "phone": "03-3981-1111",
        "website": "https://www.city.toshima.lg.jp/",
        "english_support": "partial",
        "note": "Ikebukuro hub. Large Chinese and Southeast Asian community. Multilingual information available."
    }
]

OUTPUT_DIR = "data/municipalities/tokyo"

for ward in WARDS:
    filepath = os.path.join(OUTPUT_DIR, f"{ward['id']}.json")
    
    # Skip if already exists (Minato)
    if os.path.exists(filepath):
        print(f"  ⏭ {ward['en']} — already exists, skipping")
        continue
    
    data = {
        "_meta": {
            "version": "0.1.0",
            "last_updated": "2025-02-07",
            "confidence": "draft",
            "needs_verification": "Office hours, English support level, branch offices, and local tips should be verified by someone who has visited."
        },
        "municipality_id": f"tokyo/{ward['id']}",
        "names": {
            "ja": ward["ja"],
            "reading": ward["reading"],
            "romaji": ward["romaji"],
            "en": ward["en"]
        },
        "prefecture": {
            "ja": "東京都",
            "en": "Tokyo"
        },
        "type": "special_ward",
        "population_note": ward.get("note", ""),
        "offices": [
            {
                "name_ja": ward["main_office_ja"],
                "name_en": ward["main_office_en"],
                "is_main": True,
                "address_ja": ward["address_ja"],
                "address_en": ward["address_en"],
                "postal_code": ward["address_ja"].split(" ")[0].replace("〒", ""),
                "phone": ward["phone"],
                "hours": {
                    "weekday": "8:30-17:00",
                    "saturday": None,
                    "sunday": None,
                    "note_en": "Closed weekends and national holidays. Some wards offer limited Saturday services — verify."
                },
                "english_support": ward["english_support"],
                "english_support_note": "Verify English availability before visiting. Bring a Japanese-speaking friend as backup."
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
                "verified_by": None
            },
            "bank_account_personal": {
                "has_english_version": False,
                "local_tips": [],
                "last_verified": None
            },
            "bank_account_corporate": {
                "has_english_version": False,
                "local_tips": [],
                "last_verified": None
            }
        },
        "useful_links": {
            "official_website": ward["website"],
            "living_guide_en": None,
            "living_guide_ja": None
        },
        "contributors": [],
        "last_updated": "2025-02-07"
    }
    
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    print(f"  ✅ Created {ward['en']} → {filepath}")

print(f"\nDone! {len(WARDS)} wards processed.")
