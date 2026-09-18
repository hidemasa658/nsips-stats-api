"""Pydantic モデル (API リクエスト/レスポンス)。"""
from __future__ import annotations

from pydantic import BaseModel


class DrugIn(BaseModel):
    rp_no_enc: str | None = None
    rp_no: str | None = None  # 平文 (RP 集計用)
    yj_code: str | None = None
    name: str | None = None
    quantity: float | None = None
    unit: str | None = None
    form: str | None = None  # 内服/外用/その他
    dosage_form_code: str | None = None  # YJ 8文字目


class RpIn(BaseModel):
    rp_no: str | None = None
    usage_code: str | None = None
    usage_text: str | None = None
    site_text: str | None = None
    is_mixed: bool = False
    drug_count: int = 0


class DrugPricingIn(BaseModel):
    seq: str | None = None
    dispensing_fee: int | None = None  # 調剤料
    drug_fee_per_unit: int | None = None  # 薬剤料単価
    quantity: int | None = None  # 数量
    total: int | None = None  # 合計 (調剤料 + 薬剤料単価 × 数量)


class FeeIn(BaseModel):
    fee_type: str | None = None
    code_enc: str | None = None
    name_enc: str | None = None
    code: str | None = None  # 平文 (加算集計用)
    name: str | None = None  # 平文 (加算集計用)
    count: int | None = None
    points: int | None = None
    is_mix_flag: bool = False


class IngestPayload(BaseModel):
    source_id: str
    detected_at: str
    body_sanitized: str | None = None
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
