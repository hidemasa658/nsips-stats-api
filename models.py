"""Pydantic モデル (API リクエスト/レスポンス)。"""
from __future__ import annotations

from pydantic import BaseModel


class DrugIn(BaseModel):
    rp_no_enc: str | None = None
    yj_code: str | None = None
    name: str | None = None
    quantity: float | None = None
    unit: str | None = None


class FeeIn(BaseModel):
    fee_type: str | None = None
    code_enc: str | None = None
    name_enc: str | None = None
    count: int | None = None
    points: int | None = None
    is_mix_flag: bool = False


class IngestPayload(BaseModel):
    source_id: str
    detected_at: str
    clinic_code_enc: str | None = None
    clinic_name_enc: str | None = None
    prescription_date_enc: str | None = None
    doctor_name_enc: str | None = None
    drugs: list[DrugIn] = []
    fees: list[FeeIn] = []


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
