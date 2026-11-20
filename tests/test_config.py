from __future__ import annotations

from ytdigest.config import Config


def test_ollama_host_defaults_to_localhost_without_remote_worker():
    cfg = Config.model_validate({})
    assert cfg.summary.ollama_host == "http://127.0.0.1:11434"


def test_ollama_host_follows_remote_asr_worker_host():
    cfg = Config.model_validate({"asr": {"remote_url": "http://192.168.1.50:8765"}})
    assert cfg.summary.ollama_host == "http://192.168.1.50:11434"


def test_ollama_host_follows_hostname_and_ignores_path_and_trailing_slash():
    cfg = Config.model_validate({"asr": {"remote_url": "http://gpu-pc.local:8765/"}})
    assert cfg.summary.ollama_host == "http://gpu-pc.local:11434"


def test_ollama_host_follows_ipv6_worker_host():
    cfg = Config.model_validate({"asr": {"remote_url": "http://[fd00::5]:8765"}})
    assert cfg.summary.ollama_host == "http://[fd00::5]:11434"


def test_explicit_ollama_host_wins_over_remote_worker():
    cfg = Config.model_validate({
        "asr": {"remote_url": "http://192.168.1.50:8765"},
        "summary": {"ollama_host": "http://127.0.0.1:11434"},
    })
    assert cfg.summary.ollama_host == "http://127.0.0.1:11434"


def test_other_summary_settings_do_not_block_derivation():
    # [summary] mit nur einem anderen Schlüssel (z. B. model) setzt den Host nicht
    cfg = Config.model_validate({
        "asr": {"remote_url": "http://192.168.1.50:8765"},
        "summary": {"model": "gpt-oss:20b"},
    })
    assert cfg.summary.ollama_host == "http://192.168.1.50:11434"
    assert cfg.summary.model == "gpt-oss:20b"
