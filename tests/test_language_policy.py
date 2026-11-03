from core.services.language_policy import choose_call_language, detect_language, normalize_language, output_policy_violations

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


def test_call_language_ignores_short_latin_acknowledgements_but_accepts_clear_switches():
    assert choose_call_language("எனக்கு உதவி வேண்டும்", "en") == "ta"
    assert choose_call_language("Enakku balance onnu sollunga", "en") == "ta"
    assert choose_call_language("okay", "ta") == "ta"
    assert choose_call_language("4477", "si") == "si"
    assert choose_call_language("Please explain the available packages in English", "ta") == "en"
    assert choose_call_language("speak sinhala please", "ta") == "si"
