from district_court_api_scraper.captcha import CaptchaAttemptPolicy


def test_captcha_policy_transitions_ocr_to_gemini_to_incomplete() -> None:
    policy = CaptchaAttemptPolicy(local_attempts=5, gemini_attempts=5)
    assert [policy.stage_for_attempt(i) for i in range(1, 6)] == ["OCR"] * 5
    assert [policy.stage_for_attempt(i) for i in range(6, 11)] == ["GEMINI"] * 5
    assert policy.stage_for_attempt(11) == "INCOMPLETE"

