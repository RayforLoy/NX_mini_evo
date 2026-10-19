import pytest

from nxmini.traces import inventory


def test_synthetic_trace(tmp_path):
    path = tmp_path / "synthetic.csv"
    path.write_text("Time [s],CH0,CH1\n0,0,1\n0.000001,1,1\n0.000002,0,1\n0.000003,1,0\n")
    report = inventory(path)
    assert report["channels"]["CH0"]["transitions"] == 3
    assert report["channels"]["CH1"]["transitions"] == 1
    assert report["channels"]["CH0"]["median_edge_interval_s"] == pytest.approx(1e-6)
    assert report["protocol"] == "unidentified"
    assert not report["nx_m_pinout_confirmed"]


def test_bad_trace(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("Time [s],CH0\n0.2,0\n0.1,1\n")
    with pytest.raises(ValueError):
        inventory(path)


@pytest.mark.parametrize("timestamp", ["nan", "inf", "-inf"])
def test_nonfinite_time_rejected(tmp_path, timestamp):
    path = tmp_path / "bad.csv"
    path.write_text(f"Time [s],CH0\n{timestamp},0\n")
    with pytest.raises(ValueError, match="finite"):
        inventory(path)
