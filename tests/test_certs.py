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
"""CertificateSet: a CA and certificates it signs, as PEM files."""
import ipaddress

from cryptography import x509
from cryptography.x509.oid import ExtendedKeyUsageOID

from volttrontesting.certs import CertificateSet


def _load(path):
    return x509.load_pem_x509_certificate(path.read_bytes())


def test_server_certificate_is_signed_by_the_ca_and_names_the_host(tmp_path):
    certs = CertificateSet(tmp_path)
    server = certs.server("web", dns=["localhost", "platform.test"], ips=["127.0.0.1"])
    cert, ca = _load(server.cert), _load(certs.ca.cert)
    cert.verify_directly_issued_by(ca)
    sans = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert sans.get_values_for_type(x509.DNSName) == ["localhost", "platform.test"]
    assert sans.get_values_for_type(x509.IPAddress) == [ipaddress.ip_address("127.0.0.1")]
    usage = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    assert ExtendedKeyUsageOID.SERVER_AUTH in usage
    assert (server.key.stat().st_mode & 0o777) == 0o600


def test_client_certificate_and_rsa_keys(tmp_path):
    certs = CertificateSet(tmp_path, key_type="rsa")
    client = certs.client("agent.one")
    cert = _load(client.cert)
    cert.verify_directly_issued_by(_load(certs.ca.cert))
    assert cert.subject.rfc4514_string() == "CN=agent.one"
    assert ExtendedKeyUsageOID.CLIENT_AUTH in cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    ca = _load(certs.ca.cert)
    assert ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca


def test_certs_fixture(certs):
    assert certs.ca.cert.exists()
