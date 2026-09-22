import hashlib

from app.adapters.bank_file import sanitize_name, sha256_of


def test_fingerprint_stable(tmp_path):
    """银行流水指纹输入必须稳定可重复（规格 16）。"""
    p = tmp_path / "f.bin"
    p.write_bytes(b"zjrc-bank-row")
    assert sha256_of(p) == hashlib.sha256(b"zjrc-bank-row").hexdigest()


def test_sanitize_name_keeps_chinese():
    assert sanitize_name("浙江柴本网络科技有限公司_交易明细.xlsx") == "浙江柴本网络科技有限公司_交易明细.xlsx"


def test_sanitize_name_strips_path_tricks():
    cleaned = sanitize_name("../../etc/passwd.xlsx")
    assert "/" not in cleaned and ".." not in cleaned
