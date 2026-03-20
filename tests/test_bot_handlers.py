"""Tests for pure helper functions in app/bot/handlers.py."""

import pytest
from app.bot.handlers import _extract_ticker


class TestExtractTicker:

    def test_extract_ticker_chinese_apple(self):
        """Chinese name '苹果' maps to AAPL."""
        assert _extract_ticker("苹果") == "AAPL"

    def test_extract_ticker_chinese_nvidia(self):
        """Chinese name '英伟达' maps to NVDA."""
        assert _extract_ticker("英伟达") == "NVDA"

    def test_extract_ticker_uppercase_code(self):
        """Bare uppercase ticker 'TSLA' is returned as-is."""
        assert _extract_ticker("TSLA") == "TSLA"

    def test_extract_ticker_lowercase_rejected(self):
        """Lowercase 'apple' is rejected — only uppercase codes are valid."""
        assert _extract_ticker("apple") is None

    def test_extract_ticker_mixed_text(self):
        """Chinese text with embedded uppercase ticker code."""
        assert _extract_ticker("帮我加一下MSFT") == "MSFT"

    def test_extract_ticker_mixed_text_with_space(self):
        """Chinese text with space-separated uppercase ticker."""
        assert _extract_ticker("帮我加一下 MSFT") == "MSFT"

    def test_extract_ticker_no_match(self):
        """Pure Chinese text with no ticker → returns None."""
        assert _extract_ticker("你好") is None
