from app.services.procurement_chain_service import CHAIN_STAGES, _overview_from_rows


def _row(done_keys: set[str], pending_count: int) -> dict:
    return {
        "pendingCount": pending_count,
        "stages": [
            {**stage, "done": stage["key"] in done_keys, "detail": "", "amount": None}
            for stage in CHAIN_STAGES
        ],
    }


def test_chain_overview_can_be_derived_from_preaggregated_rows():
    rows = [
        _row({stage["key"] for stage in CHAIN_STAGES}, 0),
        _row({"order", "sku", "jackyunPo"}, 2),
    ]

    result = _overview_from_rows(rows)

    assert result["total"] == 2
    assert result["pending"] == 2
    assert result["refined"] == 2
    assert result["jackyunLinked"] == 2
    assert result["inbound"] == 1
    assert result["verified"] == 1
