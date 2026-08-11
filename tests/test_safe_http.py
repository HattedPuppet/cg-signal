import unittest
from email.message import Message
import time

from cg_signal.safe_http import ResponseTooLargeError, SafeHttpClient, SafeHttpError


PUBLIC = "93.184.216.34"


def resolver_for(mapping):
    def resolve(host, port, **kwargs):
        value = mapping.get(host, PUBLIC)
        if isinstance(value, list):
            values = value
        else:
            values = [value]
        return [
            (2 if "." in address else 10, 1, 6, "", (address, port))
            for address in values
        ]
    return resolve


class FakeResponse:
    def __init__(self, status=200, body=b"ok", headers=None):
        self.status = status
        self.body = body
        self._remaining = body
        self.headers = headers or Message()

    def read(self, maximum):
        chunk, self._remaining = self._remaining[:maximum], self._remaining[maximum:]
        return chunk


class FakeConnection:
    def __init__(self, response):
        self.response = response
        self.request_args = None

    def request(self, method, target, headers=None):
        self.request_args = (method, target, headers or {})

    def getresponse(self):
        return self.response

    def close(self):
        pass


class SafeHttpTests(unittest.TestCase):
    def test_literal_non_global_classes_are_rejected(self):
        for literal in (
            "127.0.0.1", "10.0.0.1", "169.254.1.1", "100.64.0.1",
            "224.0.0.1", "0.0.0.0", "192.0.2.1", "::1", "fc00::1", "::ffff:10.0.0.1",
        ):
            with self.subTest(literal=literal):
                client = SafeHttpClient(resolver=resolver_for({literal: PUBLIC}))
                with self.assertRaises(SafeHttpError):
                    client.get(f"http://[{literal}]" if ":" in literal else f"http://{literal}")

    def test_private_only_and_mixed_dns_answers_are_rejected(self):
        for addresses in (["10.0.0.1"], [PUBLIC, "192.168.1.2"]):
            client = SafeHttpClient(resolver=resolver_for({"feed.test": addresses}))
            with self.assertRaises(SafeHttpError):
                client.get("http://feed.test/feed")

    def test_public_success_pins_exact_validated_address_and_host(self):
        connections = []

        def factory(**values):
            connections.append(values)
            connection = FakeConnection(FakeResponse(body=b"feed"))
            connections[-1]["connection"] = connection
            return connection

        client = SafeHttpClient(
            resolver=resolver_for({"feed.test": PUBLIC}),
            connection_factory=factory,
        )
        response = client.get("http://feed.test:8080/feed")
        self.assertEqual(response.body, b"feed")
        self.assertEqual(connections[0]["address"], (PUBLIC, 8080))
        self.assertEqual(connections[0]["connection"].request_args[0], "GET")
        self.assertEqual(connections[0]["connection"].request_args[2]["Host"], "feed.test:8080")

    def test_host_header_uses_normalized_ascii_idna_hostname(self):
        connections = []

        def factory(**values):
            connection = FakeConnection(FakeResponse(body=b"ok"))
            connections.append((values, connection))
            return connection

        client = SafeHttpClient(
            resolver=resolver_for({"xn--bcher-kva.example": PUBLIC}),
            connection_factory=factory,
        )
        response = client.get("http://Bücher.example/feed")
        self.assertEqual(response.body, b"ok")
        values, connection = connections[0]
        self.assertEqual(values["hostname"], "xn--bcher-kva.example")
        self.assertEqual(connection.request_args[2]["Host"], "xn--bcher-kva.example")

    def test_relative_and_cross_host_redirects_are_validated_each_hop(self):
        requested = []

        def factory(**values):
            requested.append(values["hostname"])
            headers = Message()
            if len(requested) == 1:
                headers["Location"] = "/next"
                return FakeConnection(FakeResponse(status=302, headers=headers))
            return FakeConnection(FakeResponse(body=b"done"))

        client = SafeHttpClient(
            resolver=resolver_for({"feed.test": PUBLIC, "cdn.test": PUBLIC}),
            connection_factory=factory,
        )
        self.assertEqual(client.get("http://feed.test/start").body, b"done")
        self.assertEqual(requested, ["feed.test", "feed.test"])

        def cross_factory(**values):
            headers = Message()
            if values["hostname"] == "feed.test":
                headers["Location"] = "https://cdn.test/file"
                return FakeConnection(FakeResponse(status=302, headers=headers))
            return FakeConnection(FakeResponse(body=b"cross"))

        client = SafeHttpClient(
            resolver=resolver_for({"feed.test": PUBLIC, "cdn.test": PUBLIC}),
            connection_factory=cross_factory,
        )
        self.assertEqual(client.get("http://feed.test/start").final_url, "https://cdn.test/file")

    def test_private_redirect_and_redirect_limit_are_rejected(self):
        def private_factory(**values):
            headers = Message({"Location": "http://private.test/"})
            return FakeConnection(FakeResponse(status=302, headers=headers))

        client = SafeHttpClient(
            resolver=resolver_for({"feed.test": PUBLIC, "private.test": "10.0.0.1"}),
            connection_factory=private_factory,
        )
        with self.assertRaises(SafeHttpError):
            client.get("http://feed.test/")

        def loop_factory(**values):
            headers = Message({"Location": "/loop"})
            return FakeConnection(FakeResponse(status=302, headers=headers))

        client = SafeHttpClient(resolver=resolver_for({"feed.test": PUBLIC}), connection_factory=loop_factory)
        with self.assertRaises(SafeHttpError):
            client.get("http://feed.test/", timeout=2)

    def test_https_seam_receives_original_hostname_for_sni_and_304_preserves_headers(self):
        calls = []
        response_headers = Message({"ETag": '"v1"', "Last-Modified": "today"})

        def factory(**values):
            calls.append(values)
            return FakeConnection(FakeResponse(status=304, headers=response_headers))

        client = SafeHttpClient(resolver=resolver_for({"Example.test": PUBLIC}), connection_factory=factory)
        response = client.get("https://Example.test/feed", headers={"If-None-Match": '"v1"'})
        self.assertEqual(response.status, 304)
        self.assertEqual(calls[0]["hostname"], "example.test")

    def test_response_size_cap_reads_one_byte_over_and_rejects(self):
        client = SafeHttpClient(
            resolver=resolver_for({"feed.test": PUBLIC}),
            connection_factory=lambda **_: FakeConnection(FakeResponse(body=b"12345")),
        )
        with self.assertRaises(ResponseTooLargeError):
            client.get("http://feed.test/", max_bytes=4)

    def test_slow_resolver_cannot_extend_the_overall_deadline(self):
        def slow_resolver(host, port, **kwargs):
            time.sleep(0.2)
            return [(2, 1, 6, "", (PUBLIC, port))]

        client = SafeHttpClient(resolver=slow_resolver)
        started = time.monotonic()
        with self.assertRaises(SafeHttpError):
            client.get("http://feed.test/", timeout=0.03)
        self.assertLess(time.monotonic() - started, 0.18)

    def test_byte_drip_cannot_extend_the_overall_deadline(self):
        class DripResponse(FakeResponse):
            def read(self, maximum):
                time.sleep(0.03)
                return super().read(1)

        client = SafeHttpClient(
            resolver=resolver_for({"feed.test": PUBLIC}),
            connection_factory=lambda **_: FakeConnection(DripResponse(body=b"slow")),
        )
        started = time.monotonic()
        with self.assertRaises((SafeHttpError, TimeoutError)):
            client.get("http://feed.test/", timeout=0.05)
        self.assertLess(time.monotonic() - started, 0.18)


if __name__ == "__main__":
    unittest.main()
