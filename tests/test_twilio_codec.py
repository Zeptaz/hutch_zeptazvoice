import numpy as np

from core.audio.twilio_codec import pcm16_to_ulaw_bytes, ulaw_bytes_to_pcm16


def test_twilio_codec_round_trip_preserves_audio_shape():
    duration_seconds = 0.25
    sample_rate = 24000
    samples = int(sample_rate * duration_seconds)
    t = np.arange(samples, dtype=np.float32) / sample_rate
    waveform = (np.sin(2 * np.pi * 440 * t) * 12000).astype(np.int16)

    encoded = pcm16_to_ulaw_bytes(waveform.tobytes(), sample_rate=sample_rate, target_rate=8000)
    decoded = ulaw_bytes_to_pcm16(encoded, sample_rate=8000, target_rate=16000)

    decoded_pcm = np.frombuffer(decoded, dtype=np.int16)
    assert decoded_pcm.size > 0
    assert np.max(np.abs(decoded_pcm)) > 500


def test_twilio_codec_handles_empty_payloads():
    assert pcm16_to_ulaw_bytes(b"") == b""
    assert ulaw_bytes_to_pcm16(b"") == b""
