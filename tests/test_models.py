import pytest
from pydantic import ValidationError

from models import DrugIn, FeeIn, IngestPayload


def test_ingest_payload_minimal():
    p = IngestPayload(
        source_id="abc123",
        detected_at="2026-09-18T10:00:00",
        drugs=[],
        fees=[],
    )
    assert p.source_id == "abc123"
    assert p.drugs == []
    assert p.clinic_code_enc is None


def test_ingest_payload_with_data():
    p = IngestPayload(
        source_id="src",
        detected_at="2026-09-18T10:00:00",
        clinic_code_enc="ENC1",
        clinic_name_enc="ENC2",
        prescription_date_enc="ENC3",
        doctor_name_enc="ENC4",
        drugs=[
            DrugIn(
                rp_no_enc="ENC_RP",
                yj_code="6152004F2089",
                name="ビブラマイシン錠100mg",
                quantity=22.0,
                unit="錠",
            )
        ],
        fees=[
            FeeIn(
                fee_type="7",
                code_enc="ENC_CODE",
                name_enc="ENC_NAME",
                count=1,
                points=59,
                is_mix_flag=False,
            )
        ],
    )
    assert p.drugs[0].yj_code == "6152004F2089"
    assert p.fees[0].is_mix_flag is False


def test_ingest_payload_rejects_missing_source_id():
    with pytest.raises(ValidationError):
        IngestPayload(detected_at="x", drugs=[], fees=[])  # type: ignore


def test_drug_in_optional_fields():
    d = DrugIn(yj_code="X")
    assert d.name is None
    assert d.quantity is None
