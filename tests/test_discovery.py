import threading

import pytest

from nxmini.discovery import describe, discover
from nxmini.receiver import Receiver, Store


def test_read_only_description_and_relative_service_urls(tmp_path):
    store = Store(tmp_path / "photos", tmp_path / "state")
    server = Receiver(("127.0.0.1", 0), store, "127.0.0.1", cameras=["127.0.0.1"])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        result = describe(base + "/DMS/SamsungDmsDesc.xml")
        assert result["identity"]["UDN"] == "uuid:" + store.uuid
        assert len(result["services"]) == 2
        assert result["services"][0]["SCPDURL"] == base + "/DMS/ContentDirectory1.xml"
        assert result["services"][0]["controlURL"] == base + "/upnp/control/ContentDirectory1"
        assert not result["control_requests_sent"]
        assert store.files() == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        store.close()


@pytest.mark.parametrize("url", ["file:///secret", "http://user:password@127.0.0.1/"])
def test_description_url_guards(url):
    with pytest.raises(ValueError):
        describe(url)


def test_discovery_rejects_bad_interface_and_timeout():
    with pytest.raises(ValueError):
        discover("not-an-ip")
    with pytest.raises(ValueError):
        discover("127.0.0.1", 31)
