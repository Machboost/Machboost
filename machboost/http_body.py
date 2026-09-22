"""Bounded decoding for compressed inference requests."""

import gzip
import io

MAX_BODY_BYTES = 32 * 1024 * 1024


def decode_body(raw: bytes, encoding: str = "") -> bytes:
    encoding = encoding.strip().lower()
    if len(raw) > MAX_BODY_BYTES:
        raise ValueError("request body exceeds 32 MiB")
    if encoding in {"", "identity"}:
        return raw
    try:
        if encoding == "zstd":
            import zstandard

            stream = zstandard.ZstdDecompressor().stream_reader(io.BytesIO(raw))
        elif encoding == "gzip":
            stream = gzip.GzipFile(fileobj=io.BytesIO(raw))
        else:
            raise ValueError(f"unsupported Content-Encoding: {encoding}")
        with stream:
            decoded = stream.read(MAX_BODY_BYTES + 1)
        if len(decoded) > MAX_BODY_BYTES:
            raise ValueError("decoded request body exceeds 32 MiB")
        return decoded
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"could not decode {encoding} request body") from exc
