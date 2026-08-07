"""Encryption at rest, secret masking, and log scrubbing."""

import logging

from server import security


def test_encrypt_roundtrip():
    token = security.encrypt("s3cr3t-value")
    assert token != "s3cr3t-value"
    assert security.decrypt(token) == "s3cr3t-value"


def test_try_decrypt_bad_input_returns_none():
    assert security.try_decrypt("not-a-real-token") is None


def test_mask_secret():
    assert security.mask_secret("") == ""
    assert security.mask_secret("abcd") == "••••"  # short → fully hidden
    masked = security.mask_secret("AIzaSyABCDEFGH1234567890")
    assert masked.startswith("AIza") and masked.endswith("7890") and "•" in masked
    assert "ABCDEFGH" not in masked


def test_scrub_registered_secret():
    security.register_secret("ULTRA-SECRET-TOKEN-123")
    out = security.scrub("authorization used ULTRA-SECRET-TOKEN-123 here")
    assert "ULTRA-SECRET-TOKEN-123" not in out
    assert "redacted" in out


def test_scrub_patterns():
    assert "abc.def.ghi" not in security.scrub("Authorization: Bearer abc.def.ghi")
    assert "topsecretval" not in security.scrub("?access_token=topsecretval&x=1")


def test_log_filter_redacts(caplog):
    security.install_log_scrubber()
    security.register_secret("LEAKY-TOKEN-XYZ-987654")
    log = logging.getLogger("theta.test")
    log.addFilter(security.SecretScrubFilter())
    with caplog.at_level(logging.INFO, logger="theta.test"):
        log.info("dumping token=LEAKY-TOKEN-XYZ-987654 oops")
    assert all("LEAKY-TOKEN-XYZ-987654" not in r.getMessage() for r in caplog.records)
