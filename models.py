"""Pydantic モデル (API リクエスト/レスポンス)。"""
from __future__ import annotations

from pydantic import BaseModel


class DrugIn(BaseModel):
    rp_no_enc: str | None = None
    rp_no: str | None = None  # 平文 (RP 集計用)
    yj_code: str | None = None
    name: str | None = None
    quantity: float | None = None
    total_quantity: float | None = None  # 実際の総処方量 (内服: 1回量×回数×日数、外用: quantity)
    unit_price: float | None = None  # 薬価 (円/単位、position 24)
    unit: str | None = None
    form: str | None = None  # 内服/外用/その他
    dosage_form_code: str | None = None  # YJ 8文字目


class RpIn(BaseModel):
    rp_no: str | None = None
    usage_code: str | None = None
    usage_text: str | None = None
    usage_kind: str | None = None  # 内服/頓服/外用/その他
    site_text: str | None = None
    is_mixed: bool = False
    drug_count: int = 0


class DrugPricingIn(BaseModel):
    seq: str | None = None
    dispensing_fee: int | None = None  # 調剤料
    drug_fee_per_unit: int | None = None  # 薬剤料単価
    quantity: int | None = None  # 数量
    total: int | None = None  # 合計 (調剤料 + 薬剤料単価 × 数量)
    internal_dispensing_fee: int | None = None  # 内服調剤料 (position 28) 60/10/0


class FeeIn(BaseModel):
    fee_type: str | None = None
    code_enc: str | None = None
    name_enc: str | None = None
    code: str | None = None  # 平文 (加算集計用)
    name: str | None = None  # 平文 (加算集計用)
    count: int | None = None
    points: int | None = None
    is_mix_flag: bool = False


class TotalsIn(BaseModel):
    total_points: int | None = None            # [5] 請求点数
    drug_fee: int | None = None                # [1] 薬剤料
    dispensing_fee_total: int | None = None    # [2] 調剤料
    pharmacy_mgmt_fee_total: int | None = None # [3] 薬学管理料
    dispensing_base_fee: int | None = None     # [7] 調剤基本料
    dispensing_add_fee: int | None = None      # [8] 調剤加算 (計量混合加算等)
    drug_guidance_fee: int | None = None       # [9] 服薬管理指導料
    pharmacy_mgmt_other: int | None = None     # [11] 薬学管理料 (服管以外)
    patient_copay: int | None = None           # [13] 患者負担金 (保険内のみ)
    patient_copay_total: int | None = None     # [17] 総患者負担額 (保険内+選定療養)
    senteryoyo_fee_excl_tax: int | None = None # [21] 選定療養費 (税抜、円)
    senteryoyo_tax: int | None = None          # [22] 選定療養費 消費税 (10%、円)
    # 旧名 (互換): night_holiday_fee, management_fee, long_prescription_fee
    night_holiday_fee: int | None = None       # 廃止 (旧 [8] 誤名)
    management_fee: int | None = None          # 廃止 (旧 [9] 誤名)
    long_prescription_fee: int | None = None   # 廃止 (旧 [11] 誤名)


class IngestPayload(BaseModel):
    source_id: str
    detected_at: str
    dispense_date: str | None = None    # 調剤日 YYYYMMDD (VER レコードから)
    dispensed_at: str | None = None     # 調剤日時 ISO (VER レコードから)
    body_sanitized: str | None = None
    totals: TotalsIn | None = None
    clinic_code_enc: str | None = None
    clinic_name_enc: str | None = None
    prescription_date_enc: str | None = None
    doctor_name_enc: str | None = None
    drugs: list[DrugIn] = []
    fees: list[FeeIn] = []
    rps: list[RpIn] = []
    drug_pricings: list[DrugPricingIn] = []


class IngestResponse(BaseModel):
    status: str
    prescription_id: int | None = None


class DrugStat(BaseModel):
    yj_code: str | None
    name: str | None
    unit: str | None
    n: int
    qty: float | None


class DrugsResponse(BaseModel):
    rows: list[DrugStat]


class ClinicStat(BaseModel):
    clinic_code_enc: str | None
    clinic_name_enc: str | None
    n: int


class ClinicsResponse(BaseModel):
    rows: list[ClinicStat]


class MixBreakdown(BaseModel):
    drug_count: int
    n: int


class MixResponse(BaseModel):
    total: int
    breakdown: list[MixBreakdown]


class PrescriptionOut(BaseModel):
    id: int
    source_id: str
    detected_at: str
    clinic_code_enc: str | None
    clinic_name_enc: str | None
    prescription_date_enc: str | None
    doctor_name_enc: str | None


class PrescriptionsExportResponse(BaseModel):
    rows: list[PrescriptionOut]
