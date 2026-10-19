import io
import json
from pathlib import Path
import threading
import urllib.error
import urllib.request
from html import escape
import xml.etree.ElementTree as ET

import pytest

from nxmini.receiver import CD, Receiver, Store, parse_didl, parse_xml


def info(name="SAM_0001.JPG", size=4):
    return {"filename": name, "size": size, "date": "2026-10-17T12:00:00", "mime": "image/jpeg",
            "protocol": "DLNA.ORG_PN=JPEG_LRG", "upnp_class": "object.item.imageItem"}


def create_body(name="SAM_0001.JPG", size=4):
    didl = (f'<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" xmlns:dc="http://purl.org/dc/elements/1.1/" '
            f'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/"><item>'
            f'<dc:title>{escape(name)}</dc:title><dc:date>2026-10-17</dc:date><upnp:class>object.item.imageItem</upnp:class>'
            f'<res protocolInfo="*:*:image/jpeg:DLNA.ORG_PN=JPEG_LRG" size="{size}"/></item></DIDL-Lite>')
    return (f'<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>'
            f'<u:CreateObject xmlns:u="{CD}"><ContainerID>0</ContainerID><Elements>{escape(didl)}</Elements>'
            f'</u:CreateObject></s:Body></s:Envelope>').encode()


@pytest.fixture
def store(tmp_path):
    result = Store(tmp_path / "photos", tmp_path / "state")
    yield result
    result.close()


def test_didl_and_xml_guards():
    assert parse_didl(create_body())["filename"] == "SAM_0001.JPG"
    with pytest.raises(ValueError):
        parse_xml(b'<!DOCTYPE a [<!ENTITY x "x">]><a>&x;</a>')


def test_stream_and_deduplicate(store):
    first = store.reserve(info(), "camera")
    assert store.receive(first["id"], "camera", 4, io.BytesIO(b"JPEG"))["sha256"]
    second = store.reserve(info(), "camera")
    assert store.receive(second["id"], "camera", 4, io.BytesIO(b"JPEG"))["duplicate"]
    third = store.reserve(info(), "camera")
    store.receive(third["id"], "camera", 4, io.BytesIO(b"DIFF"))
    paths = list(store.root.glob("2026-10-17/*"))
    assert len(paths) == 2
    assert {p.read_bytes() for p in paths} == {b"JPEG", b"DIFF"}


def test_interruption_retry_and_restart(store):
    row = store.reserve(info(), "camera")
    with pytest.raises(ValueError, match="Interrupted"):
        store.receive(row["id"], "camera", 4, io.BytesIO(b"JP"))
    assert not store.files()
    assert store.reserve(info(), "camera")["id"] == row["id"]
    store.receive(row["id"], "camera", 4, io.BytesIO(b"JPEG"))
    other = Store(store.root, store.state)
    try:
        assert other.files()[0]["filename"] == "SAM_0001.JPG"
        assert other.token == store.token and other.uuid == store.uuid
    finally:
        other.close()


@pytest.mark.parametrize("filename", ["../x.JPG", "a/b.JPG", "a\\b.JPG", "x:evil.JPG", "script.py", "a\0.JPG"])
def test_reject_unsafe_media(store, filename):
    with pytest.raises(ValueError):
        store.reserve(info(filename), "camera")


def test_wrong_size_and_wrong_camera(store):
    row = store.reserve(info(), "camera")
    with pytest.raises(ValueError):
        store.receive(row["id"], "other", 4, io.BytesIO(b"JPEG"))
    with pytest.raises(ValueError):
        store.receive(row["id"], "camera", 3, io.BytesIO(b"JPG"))


def test_paging_dedup_and_old_original_access(store):
    for index in range(7):
        row = store.reserve(info(f"SAM_{index:04d}.JPG"), "camera")
        store.receive(row["id"], "camera", 4, io.BytesIO(index.to_bytes(4, "little")))
    first = store.files(limit=3)
    second = store.files(limit=3, before=first[-1]["sequence"])
    third = store.files(limit=3, before=second[-1]["sequence"])
    assert len(first + second + third) == 7
    assert len({row["id"] for row in first + second + third}) == 7
    assert store.file(third[0]["id"])["filename"] == "SAM_0000.JPG"
    with pytest.raises(ValueError):
        store.files(limit=1001)


def test_list_remains_available_during_upload(store):
    row = store.reserve(info(), "camera")
    entered, resume = threading.Event(), threading.Event()
    errors = []

    class SlowSource(io.BytesIO):
        def read(self, size):
            entered.set()
            if not resume.wait(3):
                raise RuntimeError("Test upload was not released")
            return super().read(size)

    def upload():
        try:
            store.receive(row["id"], "camera", 4, SlowSource(b"JPEG"))
        except Exception as error:
            errors.append(error)

    thread = threading.Thread(target=upload)
    thread.start()
    try:
        assert entered.wait(3)
        assert store.files() == []
        with pytest.raises(ValueError, match="already active"):
            store.receive(row["id"], "camera", 4, io.BytesIO(b"JPEG"))
    finally:
        resume.set()
        thread.join(timeout=4)
    assert not thread.is_alive() and not errors
    assert len(store.files()) == 1


def test_http_soap_upload_and_token_api(store):
    server = Receiver(("127.0.0.1", 0), store, "127.0.0.1", cameras=["127.0.0.1"])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urllib.request.urlopen(base + "/DMS/SamsungDmsDesc.xml") as response:
            assert store.uuid.encode() in response.read()
        request = urllib.request.Request(base + "/upnp/control/ContentDirectory1", data=create_body(),
                                         headers={"SOAPAction": f'"{CD}#CreateObject"', "Content-Type": "text/xml"})
        with urllib.request.urlopen(request) as response:
            root = ET.fromstring(response.read())
        oid = next(e.text for e in root.iter() if e.tag.endswith("ObjectID"))
        with urllib.request.urlopen(urllib.request.Request(base + "/cd/content?didx=0_id=" + oid, data=b"JPEG")) as response:
            assert response.status == 200
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(base + "/api/files")
        assert error.value.code == 401
        auth = {"Authorization": "Bearer " + store.token}
        with urllib.request.urlopen(urllib.request.Request(base + "/api/files", headers=auth)) as response:
            files = json.load(response)["files"]
        with urllib.request.urlopen(urllib.request.Request(base + "/api/files/" + files[0]["id"], headers=auth)) as response:
            assert response.read() == b"JPEG"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
