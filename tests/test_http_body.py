import gzip
import unittest
from unittest.mock import patch

import zstandard

from machboost.http_body import decode_body


class RequestBodyTests(unittest.TestCase):
    def test_supported_encodings(self):
        body = b'{"input":"hello"}'
        for encoding, raw in [("identity", body), ("gzip", gzip.compress(body)),
                              ("zstd", zstandard.ZstdCompressor().compress(body))]:
            self.assertEqual(decode_body(raw, encoding), body)

    def test_expansion_limit(self):
        for encoding, raw in [("gzip", gzip.compress(b"a" * 1000)),
                              ("zstd", zstandard.ZstdCompressor().compress(b"a" * 1000))]:
            with patch("machboost.http_body.MAX_BODY_BYTES", 100):
                with self.assertRaises(ValueError):
                    decode_body(raw, encoding)

    def test_unknown_encoding(self):
        with self.assertRaisesRegex(ValueError, "unsupported"):
            decode_body(b"abc", "unknown")
