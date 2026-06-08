from __future__ import annotations

import json
from pathlib import Path

from district_court_api_scraper.captcha import CaptchaAttemptPolicy
from district_court_api_scraper.config import AppConfig
from district_court_api_scraper.ecourts_client import PdfDownloadResult
from district_court_api_scraper.pipelines import PipelineRunner


FIXTURES = Path(__file__).parent / "fixtures"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class StubCaptchaSolver:
    def solve_captcha(self, _image: bytes, **_kwargs: object) -> str | None:
        return "abc123"


class FakeClient:
    def __init__(self) -> None:
        self.act_html = _read("submit_act_case_list.html")
        self.detail_html = _read("case_detail.html")
        self.hearing_html = _read("hearing_detail.html")

    def get_captcha_image(self) -> bytes | None:
        return b"fake-image"

    def submit_act(self, **_kwargs: str):
        return {"status": 1, "act_data": self.act_html, "div_captcha": "<img>"}

    def view_history(self, **_kwargs: str):
        return {"status": 1, "data_list": self.detail_html}

    def view_business(self, _payload: dict[str, str]):
        return {"status": 1, "data_list": self.hearing_html}

    def search_by_cnr(self, *, cino: str, captcha_code: str):
        assert cino
        assert captcha_code
        return {"status": 1, "casetype_list": self.detail_html}

    def download_pdf(self, params: dict[str, str]) -> PdfDownloadResult:
        assert "filename" in params
        return PdfDownloadResult(ok=True, content=b"%PDF-1.4\nfake\n", source_url="https://example.test/fake.pdf")

    @staticmethod
    def is_invalid_captcha(_payload: dict[str, object] | None) -> bool:
        return False


def test_end_to_end_act_pipeline_and_export(tmp_path: Path) -> None:
    config = AppConfig.default(project_root=tmp_path)
    runner = PipelineRunner(
        config=config,
        run_id="it_act",
        client=FakeClient(),
        captcha_solver=StubCaptchaSolver(),
        captcha_policy=CaptchaAttemptPolicy(local_attempts=5, gemini_attempts=5),
    )
    runner.run_list_acts(
        state_code="4",
        dist_code="9",
        court_complex_code="1040097",
        est_code="18",
        act_code="18",
        section="138",
        case_status="Pending",
    )
    runner.run_fetch_details()
    cases_csv, hearings_csv = runner.export()

    assert runner.run_paths.case_list_items_jsonl.exists()
    assert runner.run_paths.case_details_jsonl.exists()
    assert runner.run_paths.hearings_jsonl.exists()
    assert cases_csv.exists()
    assert hearings_csv.exists()

    case_rows = runner.run_paths.case_details_jsonl.read_text(encoding="utf-8").strip().splitlines()
    hearing_rows = runner.run_paths.hearings_jsonl.read_text(encoding="utf-8").strip().splitlines()
    assert len(case_rows) == 1
    assert len(hearing_rows) == 1

    case_payload = json.loads(case_rows[0])
    assert len(case_payload["final_order_downloads"]) == 1
    assert Path(case_payload["final_order_downloads"][0]["file"]).parent.name == "KLTV350016432025"

    pdf_files = list(runner.run_paths.raw_pdfs.rglob("*.pdf"))
    assert len(pdf_files) >= 2

    header = cases_csv.read_text(encoding="utf-8").splitlines()[0]
    assert "cino" in header
    assert "pdf_count" in header


def test_end_to_end_cnr_pipeline(tmp_path: Path) -> None:
    config = AppConfig.default(project_root=tmp_path)
    cnr_file = tmp_path / "cnrs.txt"
    cnr_file.write_text("KLTV350016432025\n", encoding="utf-8")
    runner = PipelineRunner(
        config=config,
        run_id="it_cnr",
        client=FakeClient(),
        captcha_solver=StubCaptchaSolver(),
        captcha_policy=CaptchaAttemptPolicy(local_attempts=5, gemini_attempts=5),
    )
    runner.run_fetch_cnr(input_path=cnr_file)
    runner.export()

    case_lines = runner.run_paths.case_details_jsonl.read_text(encoding="utf-8").strip().splitlines()
    hearing_lines = runner.run_paths.hearings_jsonl.read_text(encoding="utf-8").strip().splitlines()
    assert len(case_lines) == 1
    assert len(hearing_lines) == 1

    payload = json.loads(case_lines[0])
    assert payload["source"] == "cnr"
    assert payload["cino"] == "KLTV350016432025"
