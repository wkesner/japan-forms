# Project Status

*Last updated: 2026-02-11*

## Overview

| Metric | Count |
|--------|-------|
| Walkthroughs generated | 50 |
| Wards covered | 23 (Tokyo) |
| Dictionary entries | 582 |

## Document Types

| Document Type | Status | Notes |
|---------------|--------|-------|
| Residence Registration (住民異動届) | **Active** | All 23 Tokyo wards, primary focus |
| Trash Disposal Guides (ゴミの出し方) | **Sourced** | 17 wards downloaded, pipeline adaptation needed |
| National Health Insurance (国民健康保険) | **Sourced** | 23/23 wards downloaded |
| Bank Account (口座開設) | **Done** | JP Post personal + corporate |
| Pension Withdrawal (脱退一時金) | **Done** | National form, single walkthrough |
| Seal Registration (印鑑登録) | **Planned** | Not yet sourced |

## Tokyo Special Wards (23区) — Residence Registration

All 23 wards have at least one walkthrough. Quality varies by source PDF type.

| Ward | Walkthroughs | Source Type | Quality | Notes |
|------|:-----------:|:-----------:|:-------:|-------|
| 足立区 Adachi | 2 | Text | Good |  |
| 荒川区 Arakawa | 2 | Text | Good |  |
| 文京区 Bunkyo | 2 | OCR | Good | Scanned form, easyOCR positioning |
| 千代田区 Chiyoda | 1 | Text | OK |  |
| 中央区 Chuo | 1 | Text | OK |  |
| 江戸川区 Edogawa | 2 | Text | Good |  |
| 板橋区 Itabashi | 4 | Text | Good |  |
| 葛飾区 Katsushika | 1 | Text | OK |  |
| 北区 Kita | 1 | Text | OK |  |
| 江東区 Koto | 4 | Text | Good |  |
| 目黒区 Meguro | 2 | Text | Good |  |
| 港区 Minato | 2 | Text | Good |  |
| 中野区 Nakano | 2 | Text | Good |  |
| 練馬区 Nerima | 3 | Text | Good |  |
| 大田区 Ota | 2 | Text | Good |  |
| 世田谷区 Setagaya | 2 | OCR | Good | Scanned form, easyOCR positioning |
| 渋谷区 Shibuya | 1 | Text | OK |  |
| 品川区 Shinagawa | 1 | Text | OK |  |
| 新宿区 Shinjuku | 5 | Text | Good |  |
| 杉並区 Suginami | 3 | Text | Good |  |
| 墨田区 Sumida | 3 | Text | Good |  |
| 台東区 Taito | 3 | Text | Good |  |
| 豊島区 Toshima | 1 | Text | OK |  |

## Trash Disposal Guides (ゴミの出し方)

Downloaded for 17/23 Tokyo wards. Pipeline adaptation needed before translation.

| Status | Wards |
|--------|-------|
| **Text-based (13)** | Adachi, Bunkyo, Edogawa, Meguro, Minato, Nakano, Nerima, Ota, Setagaya, Shibuya, Shinjuku, Suginami, Taito |
| **Scanned (4)** | Chiyoda, Kita, Shinagawa, Sumida |
| **Not sourced (6)** | Arakawa, Chuo, Itabashi, Katsushika, Koto, Toshima |
| **Translated** | *None yet* |

## National Health Insurance (国民健康保険)

Scraping targets configured for 23/23 Tokyo wards. Applications are ward-specific, similar to residence registration.

| Status | Wards |
|--------|-------|
| **Downloaded (23)** | Adachi, Arakawa, Bunkyo, Chiyoda, Chuo, Edogawa, Itabashi, Katsushika, Kita, Koto, Meguro, Minato, Nakano, Nerima, Ota, Setagaya, Shibuya, Shinagawa, Shinjuku, Suginami, Sumida, Taito, Toshima |
| **Not yet scraped (0)** |  |
| **Translated** | *None yet* |

## Regional Coverage Roadmap

### Phase 1: Tokyo (東京都) — In Progress

| Area | Municipalities | Residence | Trash | NHI | Coverage |
|------|:-:|:-:|:-:|:-:|:-:|
| **23 Special Wards** | 23 | 23/23 | 0/23 | 23/23 | **100%** |
| Tama Region (多摩地域) | 30 | 0 | 0 | 0 | 0% |
| **Tokyo Total** | 53 | 23/53 | 0/53 | 23/53 | **43%** |

### Phase 2: Kanto Region (関東) — Planned

| Prefecture | Capital | Municipalities | Status |
|-----------|---------|:-:|--------|
| 東京都 Tokyo | — | 53 | **Phase 1** |
| 神奈川県 Kanagawa | Yokohama | 33 | Not started |
| 埼玉県 Saitama | Saitama | 63 | Not started |
| 千葉県 Chiba | Chiba | 54 | Not started |
| 茨城県 Ibaraki | Mito | 44 | Not started |
| 栃木県 Tochigi | Utsunomiya | 25 | Not started |
| 群馬県 Gunma | Maebashi | 35 | Not started |
| **Kanto Total** | — | **307** | **7%** |

### Phase 3: Kansai Region (関西) — Planned

| Prefecture | Capital | Municipalities | Status |
|-----------|---------|:-:|--------|
| 大阪府 Osaka | Osaka | 43 | Not started |
| 京都府 Kyoto | Kyoto | 26 | Not started |
| 兵庫県 Hyogo | Kobe | 41 | Not started |
| 奈良県 Nara | Nara | 39 | Not started |
| 滋賀県 Shiga | Otsu | 19 | Not started |
| 和歌山県 Wakayama | Wakayama | 30 | Not started |
| **Kansai Total** | — | **198** | **0%** |

### Phase 4: Remaining Regions — Future

| Region | Prefectures | Municipalities (approx.) | Status |
|--------|:-:|:-:|--------|
| Chubu (中部) | 9 | ~270 | Not started |
| Kyushu (九州) | 8 | ~230 | Not started |
| Tohoku (東北) | 6 | ~220 | Not started |
| Chugoku (中国) | 5 | ~110 | Not started |
| Shikoku (四国) | 4 | ~95 | Not started |
| Hokkaido (北海道) | 1 | ~180 | Not started |
| Okinawa (沖縄) | 1 | ~41 | Not started |

### National Summary

| Metric | Count |
|--------|-------|
| Total municipalities in Japan | ~1,741 |
| Municipalities with any coverage | 23 |
| **National coverage** | **1.3%** |

## Technical Notes

- **OCR pipeline**: easyOCR for positioning + dictionary pre-filter + Claude Vision for classification
- **Auto page classification**: Pages with <50 extractable characters route to OCR automatically
- **Translation hierarchy**: Dictionary (free) → Fragment match (free) → Claude Sonnet (API)
