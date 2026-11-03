"""SCHWAB_STREAM=0: the scanner never opens a Schwab streamer and polls every symbol instead."""
import pytest

from tests.test_schwab_provider import _fake_feed


def test_no_streamer_is_opened_and_every_symbol_is_polled(monkeypatch, capsys):
    monkeypatch.setenv("SCHWAB_STREAM", "0")
    sent = []
    feed, fake_stream = _fake_feed(monkeypatch, sent)
    monkeypatch.setattr(fake_stream, "__init__", lambda *_: pytest.fail("opened a Schwab streamer"))
    symbols = [f"S{i}" for i in range(4000)]
    feed.subscribe_minute_bars(symbols, lambda bar: None)

    assert sent == []                                      # nothing subscribed on any stream
    assert feed._stream is None
    assert feed.streamed_symbols == [] and feed.quote_streamed_symbols == []
    assert feed.polled_symbols == symbols                  # all of them, in the order given
    assert feed.unstreamed_symbols == []
    assert "Schwab streamer off (SCHWAB_STREAM=0): all 4,000 symbols" in capsys.readouterr().out
    feed.stop_stream()                                     # with no streamer to stop


def test_no_streamer_without_synthetic_bars_refuses_to_start(monkeypatch):
    monkeypatch.setenv("SCHWAB_STREAM", "0")
    monkeypatch.setenv("SCHWAB_SYNTHETIC_BARS", "0")
    feed, _ = _fake_feed(monkeypatch, [])
    with pytest.raises(RuntimeError, match="SCHWAB_SYNTHETIC_BARS"):
        feed.subscribe_minute_bars(["SPY"], lambda bar: None)


def test_streaming_stays_the_default(monkeypatch):
    monkeypatch.delenv("SCHWAB_STREAM", raising=False)
    sent = []
    feed, _ = _fake_feed(monkeypatch, sent)
    feed.subscribe_minute_bars(["SPY", "AAPL"], lambda bar: None)
    assert sent == [("CHART_EQUITY", ["SPY", "AAPL"])]
