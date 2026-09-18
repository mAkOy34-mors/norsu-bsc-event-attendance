"""
Generate a self-signed TLS certificate for local HTTPS development.

Creates cert/dev.crt + cert/dev.key with SANs covering localhost, the
machine's hostname, and all detected LAN IPs — so phones on the same Wi-Fi
can reach https://<pc-ip>:8001/ and the browser camera (getUserMedia) works.

Usage:  python make_dev_cert.py
"""
import datetime
import ipaddress
import os
import socket

from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa

CERT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cert")
CERT_PATH = os.path.join(CERT_DIR, "dev.crt")
KEY_PATH = os.path.join(CERT_DIR, "dev.key")


def lan_ips():
    ips = {"127.0.0.1"}
    # Well-known adapter addresses (kept in sync with this project's .env)
    ips.update({"192.168.180.147", "192.168.48.1", "10.0.28.161"})
    try:  # hostname resolution
        ips.add(socket.gethostbyname(socket.gethostname()))
    except OSError:
        pass
    try:  # default-route interface (the one that reaches the internet)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return {ip for ip in ips if ip}


def main():
    os.makedirs(CERT_DIR, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    name = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, socket.gethostname() or "localhost"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Dev"),
        ]
    )

    san_names = [x509.DNSName("localhost")]
    for ip in sorted(lan_ips()):
        san_names.append(x509.IPAddress(ipaddress.ip_address(ip)))
    hostname = socket.gethostname()
    if hostname:
        san_names.append(x509.DNSName(hostname))

    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=825))  # Android caps >825d
        .add_extension(
            x509.SubjectAlternativeName(san_names),
            critical=False,
        )
        .add_extension(
            x509.BasicConstraints(ca=False, path_length=None),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )

    with open(KEY_PATH, "wb") as f:
        f.write(
            key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
    with open(CERT_PATH, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))

    print(f"Certificate written: {CERT_PATH}")
    print(f"Private key written: {KEY_PATH}")
    print("SANs:", ", ".join(str(getattr(n, "value", n)) for n in san_names))


if __name__ == "__main__":
    main()
