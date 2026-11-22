from codefoundry.credits import ensure_credits, ensure_notice
from codefoundry.receipts import ReceiptStore


def test_credits_and_notice(tmp_path):
    ensure_credits(tmp_path, source="A/B", sha="0"*40, license_spdx="MIT")
    ensure_notice(tmp_path, source="A/B", sha="0"*40)
    c = (tmp_path / "CREDITS.md").read_text()
    n = (tmp_path / "NOTICE").read_text()
    assert "A/B" in c and "MIT" in c
    assert "A/B" in n


def test_receipt_store(tmp_path):
    s = ReceiptStore(tmp_path)
    assert s.get("b1", "A/B") is None
    s.put("b1", "A/B", {"destination_repo_id": 1})
    assert s.get("b1", "A/B")["destination_repo_id"] == 1
