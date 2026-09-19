"""NSIPS .txt をレコード種別ごとにパース。record 1 (患者情報) は必ず破棄する。"""
from __future__ import annotations


# YJ コード 8 文字目 (剤形記号) の分類マップ
_FORM_INTERNAL = set("FKSGREDCLB")  # 内服系: 錠/カプセル/散/顆粒/咀嚼/内服液/エキス 等
_FORM_EXTERNAL = set("MNQXUP")      # 外用系: 軟膏/クリーム/ローション/貼付/パップ/坐剤


def classify_form(yj_code: str | None) -> str:
    """YJ コードから 剤形分類 (内用/外用/注射/その他) を返す。

    判別優先順位:
    1. YJ 5-7桁 投与経路 (001-399=内用 400-699=注射 700-999=外用) — マスタ規則
    2. YJ 8文字目 letter mapping — fallback
    """
    if not yj_code or len(yj_code) < 8:
        return "その他"

    # 優先: 5-7桁 投与経路
    if len(yj_code) >= 7:
        route_code = yj_code[4:7]
        if route_code.isdigit():
            n = int(route_code)
            if 1 <= n <= 399:
                return "内用"
            elif 400 <= n <= 699:
                return "注射"
            elif 700 <= n <= 999:
                return "外用"

    # フォールバック: 8桁目
    letter = yj_code[7]
    if letter in _FORM_EXTERNAL:
        return "外用"
    if letter in _FORM_INTERNAL:
        return "内用"
    return "その他"


def get_dosage_form_code(yj_code: str | None) -> str | None:
    """YJ コード 8 文字目 (剤形記号) を返す。"""
    if not yj_code or len(yj_code) < 8:
        return None
    return yj_code[7]


def sanitize_body(body: str) -> str:
    """NSIPS 本文から record 1 (患者情報) 行を削除して返す。"""
    return "\n".join(
        line for line in body.splitlines() if not line.lstrip().startswith("1,")
    )


def _col(fields: list[str], idx: int) -> str | None:
    if idx < 0 or idx >= len(fields):
        return None
    v = fields[idx].strip()
    return v if v else None


def _to_float(v: str | None) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _to_int(v: str | None) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except ValueError:
        return None


def _extract_quantity(fields: list[str], form: str) -> float | None:
    """
    処方量 (position 16):
    - 外用: 総処方量 (例: 軟膏 30g)
    - 内服: 1回量 (例: 錠 1錠) — 総数量は record 3 の 回数×日数 と併せて算出必要
    - その他: position 16
    """
    return _to_float(_col(fields, 16))


def _extract_unit_price(fields: list[str]) -> float | None:
    """薬価 (position 24、円/単位)。例: ヘパリン軟膏 6.3円/g、ビブラマイシン錠 22.1円/錠"""
    return _to_float(_col(fields, 24))


def parse_nsips(body: str) -> dict:
    """NSIPS .txt をパースし、record 1 を除いた辞書を返す。

    Returns:
        {
          "prescription": {...},
          "drugs": [{"rp_no","yj_code","name","quantity","unit","form","dosage_form_code"}, ...],
          "fees": [{"fee_type","code","name","count","points"}, ...],
          "rps": [
            {
              "rp_no": "1",
              "usage_code": "244",
              "usage_text": "分3 毎食後服用",
              "site_text": "混合" or "",   # record 3 field 5 (混合フラグ用)
              "is_mixed": False,             # record 3 に "混合" 含む OR 同 RP に 2+ record 4
              "drug_count": 1,               # 集計後
            }, ...
          ],
        }
    """
    result: dict = {
        "prescription": {},
        "drugs": [],
        "fees": [],
        "rps": [],
        "drug_pricings": [],  # record 6 基本料 (調剤料+薬剤料単価×数量=合計)
        "totals": {},          # record 5 全体集計 (請求点数、患者負担金 等)
    }
    rps_by_no: dict[str, dict] = {}

    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        fields = line.split(",")
        rec = fields[0]

        if rec == "1":
            continue  # 患者情報破棄

        if rec.startswith("VER"):
            # ヘッダ: VER010603,20260919091259,Medicom Pharnes,...
            # フィールド 1 の 先頭 8 桁 = 調剤日 (YYYYMMDD)、14 桁で調剤日時
            ts = _col(fields, 1)
            if ts and len(ts) >= 8 and ts[:8].isdigit():
                result["dispense_date"] = ts[:8]  # "20260919"
                if len(ts) >= 14 and ts[:14].isdigit():
                    result["dispensed_at"] = (
                        f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]}T{ts[8:10]}:{ts[10:12]}:{ts[12:14]}"
                    )
            continue

        if rec == "5":
            # record 5: 全体集計。
            # [5] 請求点数、[7] 調剤基本料、[8] 夜間・休日等加算、
            # [9] 薬学管理料、[11] 長期処方加算 (28日以上=60点 / 27日以下=10点)、
            # [13] 患者負担金 (3割等)
            result["totals"] = {
                "total_points": _to_int(_col(fields, 5)),
                "dispensing_base_fee": _to_int(_col(fields, 7)),
                "night_holiday_fee": _to_int(_col(fields, 8)),
                "management_fee": _to_int(_col(fields, 9)),
                "long_prescription_fee": _to_int(_col(fields, 11)),
                "patient_copay": _to_int(_col(fields, 13)),
            }

        elif rec == "2":
            result["prescription"] = {
                "prescription_date": _col(fields, 4),
                "clinic_code": _col(fields, 12),
                "clinic_name": _col(fields, 14),
                "doctor_name": None,
            }

        elif rec == "3":
            rp_no = _col(fields, 1)
            usage_code = _col(fields, 2)
            usage_text = _col(fields, 3)
            site_text = _col(fields, 5)  # 「混合」等
            days = _to_int(_col(fields, 10))          # 日数
            times_per_day = _to_int(_col(fields, 11))  # 1日の回数
            is_mixed_marker = site_text == "混合"
            rps_by_no[rp_no or ""] = {
                "rp_no": rp_no,
                "usage_code": usage_code,
                "usage_text": usage_text,
                "site_text": site_text,
                "days": days,
                "times_per_day": times_per_day,
                "is_mixed": is_mixed_marker,
                "drug_count": 0,
            }

        elif rec == "4":
            rp_no = _col(fields, 2)
            yj_code = _col(fields, 4)
            form = classify_form(yj_code)
            result["drugs"].append(
                {
                    "rp_no": rp_no,
                    "yj_code": yj_code,
                    "name": _col(fields, 8),
                    "quantity": _extract_quantity(fields, form),
                    "unit_price": _extract_unit_price(fields),  # 薬価 円/単位
                    "unit": _col(fields, 18),
                    "form": form,
                    "dosage_form_code": get_dosage_form_code(yj_code),
                }
            )
            # RP に drug count
            if rp_no and rp_no in rps_by_no:
                rps_by_no[rp_no]["drug_count"] += 1

        elif rec == "7":
            # record 7: 加算料。code[2], name[3], count[4], points[5]
            result["fees"].append(
                {
                    "fee_type": rec,
                    "code": _col(fields, 2),
                    "name": _col(fields, 3),
                    "count": _to_int(_col(fields, 4)),
                    "points": _to_int(_col(fields, 5)),
                }
            )

        elif rec == "6":
            # record 6 は 2 バリアント:
            # (a) 加算料: [8]=コード, [9]=名 (例: 計量混合加算)
            # (b) 基本料: [2]=調剤料, [3]=薬剤料単価, [4]=数量, [5]=合計 (剤ごとに1行)
            code = _col(fields, 8)
            name = _col(fields, 9)
            if code or name:
                # (a) 加算料
                result["fees"].append(
                    {
                        "fee_type": rec,
                        "code": code,
                        "name": name,
                        "count": _to_int(_col(fields, 4)),
                        "points": _to_int(_col(fields, 6)),
                    }
                )
            else:
                # (b) 基本料 (剤ごと)
                result["drug_pricings"].append(
                    {
                        "seq": _col(fields, 1),
                        "dispensing_fee": _to_int(_col(fields, 2)),
                        "drug_fee_per_unit": _to_int(_col(fields, 3)),
                        "quantity": _to_int(_col(fields, 4)),
                        "total": _to_int(_col(fields, 5)),
                        # 末尾 (position 28): 内服調剤料 (28日以上=60点, 27日以下=10点, 外用=0)
                        "internal_dispensing_fee": _to_int(_col(fields, 28)),
                    }
                )

    # is_mixed は site_text == "混合" のみで判定 (drug_count >= 2 は誤検出 = 内服の複数剤)
    for rp in rps_by_no.values():
        result["rps"].append(rp)

    # 内用の総数量 = 1回量 × 回数/日 × 日数
    # 外用は quantity (position 16) が既に総処方量なのでそのまま
    for d in result["drugs"]:
        if d.get("form") in ("内用", "内服"):
            rp = rps_by_no.get(d.get("rp_no") or "")
            if rp and rp.get("times_per_day") and rp.get("days"):
                try:
                    d["total_quantity"] = float(d["quantity"]) * rp["times_per_day"] * rp["days"]
                except (TypeError, ValueError):
                    d["total_quantity"] = None
            else:
                d["total_quantity"] = None
        else:
            # 外用/その他: quantity がそのまま総量
            d["total_quantity"] = d.get("quantity")

    return result
