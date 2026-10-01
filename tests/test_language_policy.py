from core.services.language_policy import detect_language, normalize_language, output_policy_violations

def test_language_aliases_allow_only_english_sinhala_tamil():
    assert normalize_language("English") == "en"
    assert normalize_language("si-LK") == "si"
    assert normalize_language("ta_IN") == "ta"
    try:
        normalize_language("fr")
    except ValueError:
        pass
    else:
        raise AssertionError("unsupported language should fail")

def test_detects_sinhala_tamil_and_mixed_english_turns():
    assert detect_language("mata burger ekak oneh").language == "si"
    assert detect_language("Enakku burger onnu order panna mudiyuma?").language == "ta"

def test_tamil_output_policy_rejects_sinhala_markers():
    assert output_policy_violations("hari, I can help", "ta")
