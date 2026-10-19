"""Experimental Samsung PC AutoBackup protocol receiver.

Protocol reference: warwickh/pc-autobackup @ 3543e8f. This is an independent
stdlib implementation. Successful replay tests are not camera certification.
"""
from datetime import date
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from html import escape
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import socket
import sqlite3
import threading
from urllib.parse import parse_qs, urlsplit
import uuid
import xml.etree.ElementTree as ET

from .util import safe_child

CD = "urn:schemas-upnp-org:service:ContentDirectory:1"
CM = "urn:schemas-upnp-org:service:ConnectionManager:1"
DEVICE = "urn:schemas-upnp-org:device:MediaServer:1"
MAX_FILE = 16 * 1024**3


def parse_xml(data):
    if len(data) > 1024 * 1024 or b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise ValueError("Unsupported XML document")
    return ET.fromstring(data)


def local(tag):
    return tag.rsplit("}", 1)[-1]


def soap(action, values=None, service=CD):
    fields = "".join(f"<{k}>{escape(str(v))}</{k}>" for k, v in (values or {}).items())
    return (f'<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
            f's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
            f'<u:{action}Response xmlns:u="{service}">{fields}</u:{action}Response>'
            f'</s:Body></s:Envelope>').encode()


def parse_didl(body):
    root = parse_xml(body)
    elements = next((e for e in root.iter() if local(e.tag) == "Elements"), None)
    if elements is None:
        raise ValueError("CreateObject has no Elements")
    didl = list(elements)[0] if len(elements) else parse_xml((elements.text or "").encode())
    values = {local(e.tag): e.text or "" for e in didl.iter()}
    resource = next((e for e in didl.iter() if local(e.tag) == "res"), None)
    if resource is None:
        raise ValueError("CreateObject has no resource")
    protocol = resource.attrib.get("protocolInfo", "").split(":", 3)
    if len(protocol) != 4:
        raise ValueError("Invalid protocolInfo")
    return {"filename": values.get("title", ""), "date": values.get("date", ""),
            "size": int(resource.attrib.get("size", "-1")),
            "mime": protocol[2], "protocol": protocol[3],
            "upnp_class": values.get("class", "object.item.imageItem")}


class Store:
    def __init__(self, root: Path, state: Path):
        self.root = root.resolve()
        self.state = state.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.state.mkdir(parents=True, exist_ok=True)
        (self.root / ".incoming").mkdir(exist_ok=True)
        self.lock = threading.RLock()
        self.active_uploads = set()
        self.db = sqlite3.connect(self.state / "receiver.sqlite3", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("""CREATE TABLE IF NOT EXISTS objects (
            id TEXT PRIMARY KEY, filename TEXT, day TEXT, size INTEGER, mime TEXT,
            protocol TEXT, upnp_class TEXT, camera TEXT, status TEXT,
            digest TEXT, path TEXT)""")
        self.db.commit()
        self.token = self._persistent("api-token", lambda: secrets.token_urlsafe(32))
        self.uuid = self._persistent("server-uuid", lambda: "4a682b0b-0361-dbae-6155-" + uuid.uuid4().hex[-12:])

    def _persistent(self, filename, create):
        path = self.state / filename
        if not path.exists():
            try:
                with path.open("x", encoding="ascii") as output:
                    output.write(create() + "\n")
                path.chmod(0o600)
            except FileExistsError:
                pass
        return path.read_text(encoding="ascii").strip()

    def reserve(self, info, camera):
        name = info["filename"]
        if not name or len(name) > 200 or any(c in name for c in '/\\:\x00') or name in (".", ".."):
            raise ValueError("Invalid media filename")
        if Path(name).suffix.lower() not in (".jpg", ".jpeg", ".srw", ".mp4"):
            raise ValueError("Unsupported media extension")
        safe_child(self.root, name)
        if not re.fullmatch(r"[A-Za-z0-9.+_-]+/[A-Za-z0-9.+_-]+", info["mime"]):
            raise ValueError("Invalid media MIME type")
        day = date.fromisoformat(info["date"][:10]).isoformat() if info["date"] else date.today().isoformat()
        if not 0 <= info["size"] <= MAX_FILE:
            raise ValueError("Invalid media size")
        with self.lock:
            row = self.db.execute("SELECT * FROM objects WHERE filename=? AND day=? AND size=? AND camera=? AND status='pending'",
                                  (name, day, info["size"], camera)).fetchone()
            if row:
                return dict(row)
            oid = secrets.token_hex(16)
            self.db.execute("INSERT INTO objects VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                            (oid, name, day, info["size"], info["mime"], info["protocol"],
                             info["upnp_class"], camera, "pending", None, None))
            self.db.commit()
            return dict(self.db.execute("SELECT * FROM objects WHERE id=?", (oid,)).fetchone())

    def receive(self, oid, camera, length, source):
        with self.lock:
            row = self.db.execute("SELECT * FROM objects WHERE id=? AND camera=? AND status='pending'", (oid, camera)).fetchone()
            if not row or row["size"] != length:
                raise ValueError("Unknown upload or Content-Length mismatch")
            if oid in self.active_uploads:
                raise ValueError("Upload is already active")
            self.active_uploads.add(oid)
        try:
            part = self.root / ".incoming" / (oid + ".part")
            remaining, digest = length, hashlib.sha256()
            with part.open("wb") as output:
                while remaining:
                    block = source.read(min(1024 * 1024, remaining))
                    if not block:
                        raise ValueError("Interrupted upload; retry starts from byte zero")
                    output.write(block)
                    digest.update(block)
                    remaining -= len(block)
                output.flush()
                os.fsync(output.fileno())
            digest = digest.hexdigest()
            with self.lock:
                duplicate = self.db.execute("SELECT path FROM objects WHERE digest=? AND status='complete' LIMIT 1", (digest,)).fetchone()
                target = None
                if duplicate:
                    prior = (self.root / duplicate["path"]).resolve()
                    if prior.is_relative_to(self.root) and prior.is_file():
                        with prior.open("rb") as existing:
                            if hashlib.file_digest(existing, "sha256").hexdigest() == digest:
                                target = prior
                reused = target is not None
                if target is None:
                    folder = safe_child(self.root, row["day"])
                    folder.mkdir(exist_ok=True)
                    target = safe_child(folder, row["filename"])
                    if target.exists():
                        target = safe_child(folder, target.stem + "-" + oid[:12] + target.suffix)
                    # Atomic no-overwrite publication, on the same filesystem.
                    os.link(part, target)
                part.unlink()
                self.db.execute("UPDATE objects SET status='complete',digest=?,path=? WHERE id=?",
                                (digest, str(target.relative_to(self.root)).replace("\\", "/"), oid))
                self.db.commit()
            return {"sha256": digest, "path": str(target), "duplicate": reused}
        finally:
            with self.lock:
                self.active_uploads.discard(oid)

    def files(self, *, limit=1000, before=None):
        if not 1 <= limit <= 1000 or (before is not None and before <= 0):
            raise ValueError("Invalid media page")
        with self.lock:
            rows = self.db.execute("""SELECT o.digest AS id,o.filename,o.day,o.size,o.mime,o.path,o.rowid AS sequence
                FROM objects o JOIN (SELECT MAX(rowid) AS latest FROM objects WHERE status='complete' GROUP BY digest) d
                ON o.rowid=d.latest WHERE (? IS NULL OR o.rowid<?) ORDER BY o.rowid DESC LIMIT ?""",
                (before, before, limit)).fetchall()
            return [dict(row) for row in rows]

    def file(self, digest):
        with self.lock:
            row = self.db.execute("""SELECT digest AS id,filename,day,size,mime,path FROM objects
                WHERE digest=? AND status='complete' ORDER BY rowid DESC LIMIT 1""", (digest,)).fetchone()
            return dict(row) if row else None

    def close(self):
        self.db.close()


def description(name, server_uuid):
    services = "".join(
        f"<service><serviceType>{service}</serviceType><serviceId>urn:upnp-org:serviceId:{kind}</serviceId>"
        f"<controlURL>/upnp/control/{kind}1</controlURL><eventSubURL>/upnp/event/{kind}1</eventSubURL>"
        f"<SCPDURL>{kind}1.xml</SCPDURL></service>"
        for kind, service in (("ContentDirectory", CD), ("ConnectionManager", CM)))
    return (f'<?xml version="1.0"?><root xmlns="urn:schemas-upnp-org:device-1-0" '
            f'xmlns:dlna="urn:schemas-dlna-org:device-1-0" xmlns:sec="http://www.sec.co.kr/dlna">'
            f'<specVersion><major>1</major><minor>0</minor></specVersion><device>'
            f'<dlna:X_DLNADOC>DMS-1.50</dlna:X_DLNADOC><deviceType>{DEVICE}</deviceType>'
            f'<friendlyName>{escape(name)}</friendlyName><manufacturer>Samsung Electronics</manufacturer>'
            '<modelDescription>Samsung PC AutoBackup compatible research receiver</modelDescription>'
            '<modelName>WiselinkPro</modelName><modelNumber>1.0</modelNumber>'
            '<sec:ProductCap>smi,DCM10,getMediaInfo.sec,getCaptionInfo.sec</sec:ProductCap>'
            f'<UDN>uuid:{server_uuid}</UDN><serviceList>{services}</serviceList></device></root>').encode()


def scpd(connection=False):
    actions = ({"GetProtocolInfo": [("Source", "out"), ("Sink", "out")],
                "GetCurrentConnectionIDs": [("ConnectionIDs", "out")]}
               if connection else {
                   "X_BACKUP_START": [], "X_BACKUP_DONE": [("SystemOff", "in")],
                   "GetSystemUpdateID": [("Id", "out")],
                   "GetSearchCapabilities": [("SearchCaps", "out")],
                   "GetSortCapabilities": [("SortCaps", "out")],
                   "CreateObject": [("ContainerID", "in"), ("Elements", "in"), ("ObjectID", "out"), ("Result", "out")]})
    action_xml, states = [], set()
    for action, arguments in actions.items():
        items = []
        for name, direction in arguments:
            states.add(name)
            items.append(f"<argument><name>{name}</name><direction>{direction}</direction><relatedStateVariable>A_{name}</relatedStateVariable></argument>")
        action_xml.append(f"<action><name>{action}</name><argumentList>{''.join(items)}</argumentList></action>")
    state_xml = "".join(f'<stateVariable sendEvents="no"><name>A_{s}</name><dataType>string</dataType></stateVariable>' for s in sorted(states))
    return ('<?xml version="1.0"?><scpd xmlns="urn:schemas-upnp-org:service-1-0"><specVersion><major>1</major><minor>0</minor></specVersion>'
            f"<actionList>{''.join(action_xml)}</actionList><serviceStateTable>{state_xml}</serviceStateTable></scpd>").encode()


class Receiver(ThreadingHTTPServer):
    # Drain handlers before the SQLite store is closed.
    daemon_threads = False

    def __init__(self, address, store, advertise_ip, *, name="[PC]NX mini evo", cameras=()):
        self.store, self.advertise_ip, self.name = store, str(ipaddress.IPv4Address(advertise_ip)), name
        self.cameras = set(cameras)
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    server_version = "NXminiEvo/0.1"
    protocol_version = "HTTP/1.0"

    def setup(self):
        super().setup()
        self.connection.settimeout(60)

    def reply(self, status, data=b"", mime="text/xml; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def camera_allowed(self):
        return not self.server.cameras or self.client_address[0] in self.server.cameras

    def api_allowed(self):
        token = self.headers.get("Authorization", "").encode("utf-8")
        return hmac.compare_digest(token, ("Bearer " + self.server.store.token).encode())

    def do_GET(self):
        url = urlsplit(self.path)
        path = url.path
        if path == "/health":
            self.reply(200, b'{"status":"experimental","camera_verified":false}', "application/json")
        elif path.startswith("/api/files"):
            if not self.api_allowed():
                self.reply(401)
                return
            if path == "/api/files":
                try:
                    query = parse_qs(url.query)
                    limit = int(query.get("limit", ["100"])[0])
                    before = int(query["before"][0]) if "before" in query else None
                    files = self.server.store.files(limit=limit, before=before)
                except ValueError as error:
                    self.reply(400, json.dumps({"error": str(error)}).encode(), "application/json")
                    return
                cursor = files[-1]["sequence"] if len(files) == limit else None
                self.reply(200, json.dumps({"files": files, "next_before": cursor}).encode(), "application/json")
                return
            digest = path.removeprefix("/api/files/")
            item = self.server.store.file(digest) if re.fullmatch(r"[0-9a-f]{64}", digest) else None
            if not item:
                self.reply(404)
                return
            target = safe_child(self.server.store.root, item["path"])
            try:
                with target.open("rb") as source:
                    self.send_response(200)
                    self.send_header("Content-Type", item["mime"])
                    self.send_header("Content-Length", str(target.stat().st_size))
                    self.end_headers()
                    while block := source.read(1024 * 1024):
                        self.wfile.write(block)
            except FileNotFoundError:
                self.reply(404)
        elif not self.camera_allowed():
            self.reply(403)
        elif path == "/DMS/SamsungDmsDesc.xml":
            self.reply(200, description(self.server.name, self.server.store.uuid))
        elif path.endswith("/ContentDirectory1.xml"):
            self.reply(200, scpd())
        elif path.endswith("/ConnectionManager1.xml"):
            self.reply(200, scpd(connection=True))
        else:
            self.reply(404)

    def do_POST(self):
        if not self.camera_allowed():
            self.reply(403)
            return
        try:
            if self.headers.get("Transfer-Encoding"):
                raise ValueError("Chunked upload is not implemented")
            length = int(self.headers.get("Content-Length", "-1"))
            if length < 0:
                raise ValueError("Content-Length is required")
            url = urlsplit(self.path)
            if url.path == "/cd/content":
                query = parse_qs(url.query)
                oid = (query.get("didx", [""])[0]).removeprefix("0_id=")
                if not re.fullmatch(r"[0-9a-f]{32}", oid):
                    raise ValueError("Invalid upload ID")
                self.server.store.receive(oid, self.client_address[0], length, self.rfile)
                self.reply(200)
                return
            if url.path not in ("/upnp/control/ContentDirectory1", "/upnp/control/ConnectionManager1"):
                self.reply(404)
                return
            if length > 1024 * 1024:
                raise ValueError("SOAP body too large")
            body = self.rfile.read(length)
            if len(body) != length:
                raise ValueError("Truncated SOAP body")
            action = self.headers.get("SOAPAction", "").strip('"').rsplit("#", 1)[-1]
            connection = url.path.endswith("ConnectionManager1")
            allowed = {"GetProtocolInfo", "GetCurrentConnectionIDs"} if connection else {
                "X_BACKUP_START", "X_BACKUP_DONE", "CreateObject", "GetSystemUpdateID", "GetSearchCapabilities", "GetSortCapabilities"}
            if action not in allowed:
                self.reply(500, b'<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body><s:Fault><faultcode>s:Client</faultcode><faultstring>Invalid Action</faultstring><detail><UPnPError xmlns="urn:schemas-upnp-org:control-1-0"><errorCode>401</errorCode><errorDescription>Invalid Action</errorDescription></UPnPError></detail></s:Fault></s:Body></s:Envelope>')
                return
            values = {}
            if action == "CreateObject":
                row = self.server.store.reserve(parse_didl(body), self.client_address[0])
                base = f"http://{self.server.advertise_ip}:{self.server.server_port}"
                didl = (f'<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" xmlns:dc="http://purl.org/dc/elements/1.1/" '
                        f'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/" xmlns:dlna="urn:schemas-dlna-org:metadata-1-0/">'
                        f'<item id="{row["id"]}" parentID="UP_{row["day"]}" restricted="0" dlna:dlnaManaged="00000004">'
                        f'<dc:title>{escape(row["filename"])}</dc:title><res protocolInfo="http-get:*:{escape(row["mime"], quote=True)}:{escape(row["protocol"], quote=True)}" '
                        f'importUri="{base}/cd/content?didx=0_id={row["id"]}" size="{row["size"]}" dlna:resumeUpload="0" dlna:uploadedSize="0"></res>'
                        f'<upnp:class>{escape(row["upnp_class"])}</upnp:class></item></DIDL-Lite>')
                values = {"ObjectID": row["id"], "Result": didl}
            elif action == "GetSystemUpdateID":
                values = {"Id": len(self.server.store.files())}
            elif action in ("GetSearchCapabilities", "GetSortCapabilities"):
                values = {"SearchCaps" if action.startswith("GetSearch") else "SortCaps": ""}
            elif action == "GetProtocolInfo":
                values = {"Source": "", "Sink": "http-get:*:image/jpeg:*,http-get:*:video/mp4:*,http-get:*:application/octet-stream:*"}
            elif action == "GetCurrentConnectionIDs":
                values = {"ConnectionIDs": "0"}
            self.reply(200, soap(action, values, CM if connection else CD))
        except (ValueError, ET.ParseError, OSError) as error:
            self.log_error("Rejected request: %s", error)
            self.reply(400, json.dumps({"error": str(error)}).encode(), "application/json")


class Discovery(threading.Thread):
    def __init__(self, server):
        super().__init__(daemon=True)
        self.server = server
        self.stop_event = threading.Event()
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(("", 1900))
        membership = socket.inet_aton("239.255.255.250") + socket.inet_aton(server.advertise_ip)
        self.socket.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
        self.socket.settimeout(1)

    def run(self):
        while not self.stop_event.is_set():
            try:
                message, address = self.socket.recvfrom(8192)
            except socket.timeout:
                continue
            if self.server.cameras and address[0] not in self.server.cameras:
                continue
            text = message.decode("latin1")
            headers = dict(line.split(":", 1) for line in text.splitlines()[1:] if ":" in line)
            headers = {k.lower(): v.strip() for k, v in headers.items()}
            target = headers.get("st", "")
            if text.startswith("M-SEARCH * HTTP/1.1") and target in (DEVICE, "ssdp:all", "upnp:rootdevice"):
                location = f"http://{self.server.advertise_ip}:{self.server.server_port}/DMS/SamsungDmsDesc.xml"
                response = (f"HTTP/1.1 200 OK\r\nCACHE-CONTROL: max-age=1800\r\nEXT:\r\nLOCATION: {location}\r\n"
                            f"SERVER: MS-Windows/XP UPnP/1.0 PROTOTYPE/1.0\r\nST: {DEVICE}\r\n"
                            f"USN: uuid:{self.server.store.uuid}::{DEVICE}\r\nCONTENT-LENGTH: 0\r\n\r\n")
                self.socket.sendto(response.encode(), address)

    def close(self):
        self.stop_event.set()
        if self.is_alive():
            self.join(timeout=2)
        self.socket.close()


def serve(args):
    if args.host not in ("127.0.0.1", "localhost") and not args.camera_ip:
        raise ValueError("LAN receiver requires --camera-ip (repeat for multiple cameras)")
    for camera in args.camera_ip:
        ipaddress.IPv4Address(camera)
    store = Store(args.output, args.state)
    server = Receiver((args.host, args.port), store, args.advertise_ip, name=args.name, cameras=args.camera_ip)
    discovery = None
    try:
        if args.ssdp:
            discovery = Discovery(server)
            discovery.start()
        print(json.dumps({"listening": server.server_address, "uuid": store.uuid,
                          "api_token_file": str(store.state / "api-token"),
                          "hardware_verified": False}), flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if discovery:
            discovery.close()
        server.server_close()
        store.close()
