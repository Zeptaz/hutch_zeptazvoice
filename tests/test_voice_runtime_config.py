from core.system.voice_runtime_config import VoiceRuntimeConfig


def test_runtime_config_keeps_optimization_experiments_off_by_default(monkeypatch):
    for name in (
        "ZEPTAZ_KNOWLEDGE_MODE",
        "ZEPTAZ_TOOL_EXECUTION_MODE",
        "ZEPTAZ_TOOL_RESULT_MODE",
        "ZEPTAZ_RETRIEVAL_MODE",
    ):
        monkeypatch.delenv(name, raising=False)

    config = VoiceRuntimeConfig.from_environment()

    assert config.knowledge_mode == "lookup_only"
    assert config.tool_execution_mode == "sequential"
    assert config.tool_result_mode == "legacy"
    assert config.retrieval_mode == "lexical"


def test_invalid_experimental_values_fall_back_safely(monkeypatch):
    monkeypatch.setenv("ZEPTAZ_VOICE_INPUT_PACKET_MS", "-1")
    monkeypatch.setenv("ZEPTAZ_RETRIEVAL_MODE", "remote_unreviewed")
    monkeypatch.setenv("ZEPTAZ_FULL_DUPLEX_ENABLED", "perhaps")

    config = VoiceRuntimeConfig.from_environment()

    assert config.packet_duration_ms == 128
    assert config.retrieval_mode == "lexical"
    assert config.full_duplex is True
    assert len(config.fingerprint) == 16
