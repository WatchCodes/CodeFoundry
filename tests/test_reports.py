from codefoundry.reports import write_report


def test_write_report_append_only(tmp_path):
    r = {"batch_id": "b1", "source": "A/B", "status": "VERIFIED", "receipt": {"destination_sha": "a"*40}}
    p1 = write_report(r, tmp_path)
    p2 = write_report(r, tmp_path)
    assert p1 == p2
    r2 = {**r, "status": "FAILED"}
    p3 = write_report(r2, tmp_path)
    assert p3 != p1
    assert p3.exists()
