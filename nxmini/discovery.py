"""Read-only UPnP discovery and device-description capture for camera adaptation."""
import ipaddress
import socket
import time
from urllib.parse import urljoin, urlsplit
import urllib.request

from .receiver import local, parse_xml


def discover(interface: str, timeout: float = 5):
    ipaddress.IPv4Address(interface)
    if not 0 < timeout <= 30:
        raise ValueError("Discovery timeout must be 0..30 seconds")
    results = {}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as sock:
        sock.bind((interface, 0))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(interface))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        for target in ("urn:schemas-upnp-org:device:MediaServer:1", "ssdp:all"):
            request = ('M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: "ssdp:discover"\r\n'
                       f'MX: 2\r\nST: {target}\r\n\r\n').encode()
            sock.sendto(request, ("239.255.255.250", 1900))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            sock.settimeout(max(0.01, deadline - time.monotonic()))
            try:
                packet, address = sock.recvfrom(65535)
            except socket.timeout:
                break
            text = packet.decode("latin1")
            if not text.startswith("HTTP/1.1 200"):
                continue
            headers = {key.strip().lower(): value.strip() for line in text.splitlines()[1:]
                       if ":" in line for key, value in [line.split(":", 1)]}
            key = (address[0], headers.get("usn"), headers.get("location"))
            results[key] = {"source": address[0], "headers": headers}
    return {"interface": interface, "devices": list(results.values()), "camera_identified": False}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def describe(url: str):
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username:
        raise ValueError("Use an HTTP(S) device-description URL without credentials")
    request = urllib.request.Request(url, headers={"User-Agent": "NXminiEvo-Research/0.1"})
    with urllib.request.build_opener(NoRedirect).open(request, timeout=10) as source:
        data = source.read(1024 * 1024 + 1)
    root = parse_xml(data)
    services = []
    for service in root.iter():
        if local(service.tag) == "service":
            values = {local(child.tag): child.text or "" for child in service}
            for key in ("controlURL", "eventSubURL", "SCPDURL"):
                if values.get(key):
                    values[key] = urljoin(url, values[key])
            services.append(values)
    identity = {local(e.tag): e.text for e in root.iter() if local(e.tag) in ("friendlyName", "modelName", "modelNumber", "manufacturer", "UDN")}
    return {"url": url, "identity": identity, "services": services,
            "captured_bytes": len(data), "control_requests_sent": False}
