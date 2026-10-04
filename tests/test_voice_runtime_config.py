from core.system.voice_runtime_config import VoiceRuntimeConfig
import app


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


def test_v3_activity_config_uses_browser_vad_and_v2_keeps_provider_vad():
    v3 = app._realtime_input_config(protocol_version=3, end_silence_ms=700, full_duplex=True)
    v2 = app._realtime_input_config(protocol_version=2, end_silence_ms=700, full_duplex=True)
    no_barge_in = app._realtime_input_config(protocol_version=3, end_silence_ms=700, full_duplex=False)

    assert v3.automatic_activity_detection.disabled is True
    assert v3.automatic_activity_detection.silence_duration_ms is None
    assert v3.activity_handling == app.types.ActivityHandling.START_OF_ACTIVITY_INTERRUPTS
    assert v2.automatic_activity_detection.disabled is False
    assert no_barge_in.activity_handling == app.types.ActivityHandling.NO_INTERRUPTION


def test_input_transcription_hints_sri_lankan_languages_by_default(monkeypatch):
    monkeypatch.delenv("GEMINI_LIVE_INPUT_LANGUAGES", raising=False)
    config = VoiceRuntimeConfig.from_environment()
    assert config.input_language_codes == ("si-LK", "en-US", "ta-IN")
    transcription = app._input_transcription_config(config.input_language_codes)
    assert transcription.language_codes == ["si-LK", "en-US", "ta-IN"]
    assert "VAS" in transcription.custom_vocabulary


def test_input_language_override_and_invalid_value(monkeypatch):
    monkeypatch.setenv("GEMINI_LIVE_INPUT_LANGUAGES", "si-LK, en-US")
    assert VoiceRuntimeConfig.from_environment().input_language_codes == ("si-LK", "en-US")
    monkeypatch.setenv("GEMINI_LIVE_INPUT_LANGUAGES", "sinhala please")
    assert VoiceRuntimeConfig.from_environment().input_language_codes == ("si-LK", "en-US", "ta-IN")
