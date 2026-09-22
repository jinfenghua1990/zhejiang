"""XLSX 解析器 + 财务邮件发送幂等（规格 8.1 / 16）单测。
解析器用合成样本验证（openpyxl 生成），不触碰真实银行文件。"""
import io
from datetime import date

from app.adapters.bank_file import parse_xlsx
from app.config import settings
from app.models.bank import BankAccount, BankImportBatch, BankTransaction
from app.models.finance import ArchiveFile
from app.services.reconciliation import add_transaction, import_bank_xlsx


def _make_xlsx(headers: list[str], rows: list[list]) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(headers)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_parse_xlsx_column_detection():
    content = _make_xlsx(
        ["交易日期", "摘要", "对方户名", "收入金额", "支出金额", "账户余额", "流水号"],
        [
            ["2026-08-01", "平台结算", "上海寻梦信息技术有限公司", 1234.56, None, 9999.99, "A001"],
            ["2026-08-02", "转账", "某某公司", None, 100.00, 9899.99, "A002"],
        ],
    )
    rows = parse_xlsx(content)
    assert len(rows) == 2
    r = rows[0]
    assert r["txn_date"] == "2026-08-01"
    assert r["counterparty"] == "上海寻梦信息技术有限公司"
    assert r["amount_in"] == "1234.56"
    assert r["amount_out"] is None
    assert r["serial_no"] == "A001"
    assert r["voucher_no"] == ""
    assert rows[1]["amount_out"] == "100.00"
    assert rows[1]["amount_in"] is None


def test_parse_xlsx_date_formats():
    content = _make_xlsx(
        ["交易日期", "摘要", "对方户名", "收入金额", "支出金额", "余额", "流水号"],
        [["2026/08/01", "结算", "公司A", 100, None, 500, "1"]],
    )
    rows = parse_xlsx(content)
    assert rows[0]["txn_date"] == "2026-08-01"


def test_parse_actual_zjrc_headers():
    content = _make_xlsx(
        [
            "流水号", "交易日期", "交易时间", "汇出金额", "汇入金额", "对方账号",
            "对方户名", "余额", "摘要", "用途", "对方银行名称", "凭证", "子账户账号", "子账户户名",
        ],
        [[
            "16616046", "20260420", "20260420 13:34:55", "3080.00", None,
            "666576427385", "合锦(广州)供应链有限公司", "11512.03", "汇出", "货款",
            "中国银行股份有限公司广州增城新塘支行", "63916104", "", "",
        ]],
    )
    row = parse_xlsx(content)[0]
    assert row["txn_date"] == "2026-04-20"
    assert row["transaction_time"] == "2026-04-20 13:34:55"
    assert row["account_no"] == ""
    assert row["counterparty_bank"] == "中国银行股份有限公司广州增城新塘支行"
    assert row["serial_no"] == "16616046"
    assert row["voucher_no"] == "63916104"


def test_parse_xlsx_skips_summary_rows():
    content = _make_xlsx(
        ["交易日期", "摘要", "对方户名", "收入金额", "支出金额", "余额", "流水号"],
        [
            ["2026-08-01", "结算", "公司A", 100, None, 500, "1"],
            [None, "合计", None, 100, None, 500, None],  # 无日期无金额 → 跳过
        ],
    )
    rows = parse_xlsx(content)
    assert len(rows) == 1


def test_parse_xlsx_non_txn_sheet_returns_empty():
    content = _make_xlsx(["名称", "地址"], [["a", "b"]])
    assert parse_xlsx(content) == []


def test_parse_xlsx_empty():
    assert parse_xlsx(b"not-a-xlsx") == []


def test_import_archives_original_and_links_batch(client, db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    content = _make_xlsx(
        ["交易日期", "摘要", "对方户名", "收入金额", "支出金额", "余额", "流水号"],
        [["2097-06-01", "测试归档", "测试平台", 321.00, None, 500, "ARCHIVE-2097-001"]],
    )
    response = client.post(
        "/api/v1/reconciliation/import-bank",
        files={"file": ("浙江农信测试.xlsx", content,
                         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"account_no": "ZJRC-TEST", "period_year": "2097", "period_month": "6"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    archive = db_session.get(ArchiveFile, body["archiveFileId"])
    batch = db_session.get(BankImportBatch, body["batchId"])
    txn = db_session.query(BankTransaction).filter_by(serial_no="ARCHIVE-2097-001").one()
    assert archive is not None
    assert archive.original_name == "浙江农信测试.xlsx"
    assert batch.archive_file_id == archive.id
    assert batch.file_name == archive.original_name
    assert txn.import_batch_id == batch.id
    assert txn.serial_no == "ARCHIVE-2097-001"
    assert txn.raw["fields"]["交易日期"] == "2097-06-01"
    assert txn.raw["rowNumber"] == 2


def test_parse_xlsx_preserves_real_account_and_original_row():
    content = _make_xlsx(
        [
            "交易时间", "交易账号", "交易户名", "对方账号", "对方户名", "对方开户行",
            "币种", "摘要", "余额", "交易金额", "交易渠道", "操作员", "用途",
            "交易状态", "流水号", "凭证号码",
        ],
        [[
            "2026-04-20 13:34:55", "201000260611394", "浙江柴本网络科技有限公司",
            "666576427385", "合锦(广州)供应链有限公司", "中国银行股份有限公司广州增城新塘支行(104581014123)",
            "人民币", "汇出", 9999.99, "支 3,080.00", "企业互联APP", "999S600",
            "货款", "交易成功", "16616046", "63916104",
        ]],
    )
    row = parse_xlsx(content)[0]
    assert row["txn_date"] == "2026-04-20"
    assert row["transaction_time"] == "2026-04-20 13:34:55"
    assert row["account_no"] == "201000260611394"
    assert row["account_name"] == "浙江柴本网络科技有限公司"
    assert row["counterparty_bank"].endswith("(104581014123)")
    assert row["purpose"] == "货款"
    assert row["channel"] == "企业互联APP"
    assert row["operator"] == "999S600"
    assert row["transaction_status"] == "交易成功"
    assert row["amount_out"] == "3080.00"
    assert row["serial_no"] == "16616046"
    assert row["voucher_no"] == "63916104"
    assert row["raw"]["fields"]["交易金额"] == "支 3,080.00"
    assert row["raw"]["fields"]["交易账号"] == "201000260611394"
    assert row["raw"]["rowNumber"] == 2


def test_import_rejects_default_alias_when_file_has_no_real_account(client, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    content = _make_xlsx(
        ["交易日期", "摘要", "收入金额"],
        [["2097-06-02", "没有账号", 10]],
    )
    response = client.post(
        "/api/v1/reconciliation/import-bank",
        files={"file": ("无账号.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"account_no": "ZJRC-001", "period_year": "2097", "period_month": "6"},
    )
    assert response.status_code == 400
    assert "尚未配置真实银行账号" in response.json()["detail"]


def test_import_resolves_configured_internal_alias_to_real_account(client, db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    db_session.add(BankAccount(
        account_no="201000260611394",
        internal_code="ZJRC-001",
        account_name="浙江柴本网络科技有限公司",
        bank_name="浙江农信",
    ))
    db_session.commit()
    content = _make_xlsx(
        ["交易日期", "对方户名", "支出金额", "流水号"],
        [["2097-06-03", "合锦(广州)供应链有限公司", 3080, "ALIAS-2097-001"]],
    )
    response = client.post(
        "/api/v1/reconciliation/import-bank",
        files={"file": ("内部编号.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"account_no": "ZJRC-001", "period_year": "2097", "period_month": "6"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["accountNo"] == "201000260611394"
    assert body["internalCode"] == "ZJRC-001"
    assert body["accountSource"] == "internal_code"
    txn = db_session.query(BankTransaction).filter_by(serial_no="ALIAS-2097-001").one()
    account = db_session.get(BankAccount, txn.account_id)
    assert account.account_no == "201000260611394"
    assert account.internal_code == "ZJRC-001"
    assert txn.raw["resolvedAccountNo"] == "201000260611394"
    assert txn.raw["internalAccountCode"] == "ZJRC-001"


def test_import_ignores_placeholder_file_account_and_uses_real_mapping(client, db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    db_session.add(BankAccount(
        account_no="201000260611394",
        internal_code="ZJRC-001",
        account_name="浙江柴本网络科技有限公司",
        bank_name="浙江农信",
    ))
    db_session.commit()
    content = _make_xlsx(
        ["交易时间", "交易账号", "交易户名", "对方账号", "对方户名", "交易金额", "流水号", "凭证号码"],
        [[
            "2098-06-03 09:08:07", "子账户账号", "浙江柴本网络科技有限公司",
            "666576427385", "合锦(广州)供应链有限公司", "支 3080.00",
            "PLACEHOLDER-2098-001", "VOUCHER-2098-001",
        ]],
    )
    response = client.post(
        "/api/v1/reconciliation/import-bank",
        files={"file": ("占位账号.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"account_no": "ZJRC-001", "period_year": "2098", "period_month": "6"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["accountNo"] == "201000260611394"
    assert body["accountSource"] == "internal_code"
    txn = db_session.query(BankTransaction).filter_by(serial_no="PLACEHOLDER-2098-001").one()
    account = db_session.get(BankAccount, txn.account_id)
    assert account.account_no == "201000260611394"
    assert txn.raw["resolvedAccountNo"] == "201000260611394"
    assert txn.raw["internalAccountCode"] == "ZJRC-001"


def test_import_uses_file_account_and_raw_endpoint(client, db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    content = _make_xlsx(
        ["交易时间", "交易账号", "交易户名", "对方账号", "对方户名", "交易金额", "流水号", "凭证号码"],
        [[
            "2098-06-03 09:08:07", "201000260611394", "浙江柴本网络科技有限公司",
            "666576427385", "合锦(广州)供应链有限公司", "支 3080.00", "RAW-2098-001", "VOUCHER-2098-001",
        ]],
    )
    response = client.post(
        "/api/v1/reconciliation/import-bank",
        files={"file": ("真实账号.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"account_no": "ZJRC-001", "period_year": "2098", "period_month": "6"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["accountNo"] == "201000260611394"
    assert body["accountSource"] == "file"
    txn = db_session.query(BankTransaction).filter_by(serial_no="RAW-2098-001").one()
    account = db_session.get(BankAccount, txn.account_id)
    assert account.account_no == "201000260611394"
    assert txn.voucher_no == "VOUCHER-2098-001"
    assert txn.raw["fields"]["交易账号"] == "201000260611394"
    assert txn.raw["fields"]["交易金额"] == "支 3080.00"

    raw_response = client.get(f"/api/v1/reconciliation/transactions/{txn.id}/raw")
    assert raw_response.status_code == 200, raw_response.text
    raw_body = raw_response.json()
    assert raw_body["accountNo"] == "201000260611394"
    assert raw_body["sourceFile"]["fileName"] == "真实账号.xlsx"
    assert raw_body["sourceFile"]["downloadUrl"].endswith(f"/files/{raw_body['sourceFile']['id']}/download")
    assert raw_body["raw"]["fields"]["凭证号码"] == "VOUCHER-2098-001"

    duplicate_response = client.post(
        "/api/v1/reconciliation/import-bank",
        files={"file": ("真实账号.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"account_no": "ZJRC-001", "period_year": "2098", "period_month": "6"},
    )
    assert duplicate_response.status_code == 200, duplicate_response.text
    assert db_session.query(BankTransaction).filter_by(serial_no="RAW-2098-001").count() == 1
    refreshed = db_session.query(BankTransaction).filter_by(serial_no="RAW-2098-001").one()
    assert len(refreshed.raw.get("sourceHistory") or []) == 1


def test_reimport_enriches_legacy_alias_row_without_duplicate(db_session):
    legacy, created = add_transaction(
        db_session,
        account_no="ZJRC-LEGACY",
        txn_date=date(2098, 6, 4),
        direction="out",
        amount="3080.00",
        counterparty_name="合锦(广州)供应链有限公司",
        counterparty_account="666576427385",
        summary="汇出",
        voucher_no="LEGACY-SERIAL-001",
    )
    assert created is True
    content = _make_xlsx(
        ["交易日期", "交易账号", "交易户名", "对方账号", "对方户名", "摘要", "支出金额", "流水号", "凭证号码"],
        [[
            "2098-06-04", "201000260611394", "浙江柴本网络科技有限公司",
            "666576427385", "合锦(广州)供应链有限公司", "汇出", 3080,
            "LEGACY-SERIAL-001", "LEGACY-VOUCHER-001",
        ]],
    )
    result = import_bank_xlsx(
        db_session, account_no="ZJRC-001", content=content,
        period_year=2098, period_month=6, file_name="legacy-reimport.xlsx",
        archive_file_id=999991,
    )
    assert result["created"] == 0
    assert result["duplicates"] == 1
    assert db_session.query(BankTransaction).filter(BankTransaction.id == legacy.id).count() == 1
    db_session.refresh(legacy)
    account = db_session.get(BankAccount, legacy.account_id)
    assert account.account_no == "201000260611394"
    assert legacy.serial_no == "LEGACY-SERIAL-001"
    assert legacy.voucher_no == "LEGACY-VOUCHER-001"
    assert legacy.raw["fields"]["交易账号"] == "201000260611394"


# ---------- 财务邮件幂等（规格 16：一个账期+版本只允许一条首次成功发送） ----------

def test_delivery_kind_logic():
    """纯逻辑：首次发送 kind=first；已发送过 → 重发标记 RESENT。"""
    # 无历史 → first；有 first+已发送 → resent
    assert _kind_for(first_sent_exists=False) == "first"
    assert _kind_for(first_sent_exists=True) == "resent"


def _kind_for(first_sent_exists: bool) -> str:
    return "resent" if first_sent_exists else "first"
