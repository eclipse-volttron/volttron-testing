# -*- coding: utf-8 -*- {{{
# ===----------------------------------------------------------------------===
#
#                 Installable Component of Eclipse VOLTTRON
#
# ===----------------------------------------------------------------------===
#
# Copyright 2026 Battelle Memorial Institute
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy
# of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.
#
# ===----------------------------------------------------------------------===
# }}}
"""Test certificates made with ``cryptography``: a CA, server certificates and client certificates, as PEM files.

Replaces shelling out to ``openssl`` for tests that need TLS (a web platform with HTTPS, a client certificate for
mutual TLS, an IEEE 2030.5 peer). Keys are EC P-256 by default, which is quick to generate; pass ``key_type="rsa"``
where a peer needs RSA.
"""
from __future__ import annotations

import datetime
import ipaddress
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

__all__ = ["CertificateSet", "CertPair"]


@dataclass(frozen=True)
class CertPair:
    cert: Path
    key: Path


class CertificateSet:
    """A CA in ``directory`` and the certificates it signs. Files are ``<name>.crt`` and ``<name>.key``."""

    def __init__(self, directory: str | Path, ca_name: str = "volttron-test-ca", *, days: int = 30,
                 key_type: str = "ec"):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.days = days
        self.key_type = key_type
        self._ca_key = self._new_key()
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, ca_name)])
        now = self._now()
        self._ca_cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                         .public_key(self._ca_key.public_key()).serial_number(x509.random_serial_number())
                         .not_valid_before(now).not_valid_after(now + datetime.timedelta(days=days))
                         .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
                         .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True,
                                                      content_commitment=False, key_encipherment=False,
                                                      data_encipherment=False, key_agreement=False,
                                                      encipher_only=False, decipher_only=False), critical=True)
                         .add_extension(x509.SubjectKeyIdentifier.from_public_key(self._ca_key.public_key()),
                                        critical=False)
                         .sign(self._ca_key, hashes.SHA256()))
        self.ca = self._write(ca_name, self._ca_cert, self._ca_key)

    @staticmethod
    def _now():
        return datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=5)

    def _new_key(self):
        if self.key_type == "rsa":
            return rsa.generate_private_key(public_exponent=65537, key_size=2048)
        if self.key_type == "ec":
            return ec.generate_private_key(ec.SECP256R1())
        raise ValueError(f"key_type must be 'ec' or 'rsa', not {self.key_type!r}")

    def _write(self, name: str, cert: x509.Certificate, key) -> CertPair:
        cert_path, key_path = self.directory / f"{name}.crt", self.directory / f"{name}.key"
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                               serialization.NoEncryption()))
        key_path.chmod(0o600)
        return CertPair(cert_path, key_path)

    def _issue(self, name: str, usage, sans: list[x509.GeneralName]) -> CertPair:
        key = self._new_key()
        now = self._now()
        builder = (x509.CertificateBuilder()
                   .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)]))
                   .issuer_name(self._ca_cert.subject).public_key(key.public_key())
                   .serial_number(x509.random_serial_number())
                   .not_valid_before(now).not_valid_after(now + datetime.timedelta(days=self.days))
                   .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                   .add_extension(x509.ExtendedKeyUsage([usage]), critical=False)
                   .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(self._ca_key.public_key()),
                                  critical=False))
        if sans:
            builder = builder.add_extension(x509.SubjectAlternativeName(sans), critical=False)
        return self._write(name, builder.sign(self._ca_key, hashes.SHA256()), key)

    def server(self, name: str = "server", *, dns: Iterable[str] = ("localhost", ),
               ips: Iterable[str] = ("127.0.0.1", )) -> CertPair:
        """A server certificate valid for the given host names and IP addresses."""
        sans = [x509.DNSName(d) for d in dns] + [x509.IPAddress(ipaddress.ip_address(i)) for i in ips]
        return self._issue(name, ExtendedKeyUsageOID.SERVER_AUTH, sans)

    def client(self, name: str = "client") -> CertPair:
        """A client certificate whose common name is ``name``."""
        return self._issue(name, ExtendedKeyUsageOID.CLIENT_AUTH, [])
