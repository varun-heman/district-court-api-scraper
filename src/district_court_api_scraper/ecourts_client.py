from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

from .parsers import normalize_pdf_params
from .transport import ECourtsTransport, HttpResponse


INVALID_CAPTCHA_RE = re.compile(r"invalid\s*captcha", re.IGNORECASE)


@dataclass(slots=True)
class PdfDownloadResult:
    ok: bool
    content: bytes | None
    source_url: str | None
    error: str | None = None


class ECourtsClient:
    def __init__(self, transport: ECourtsTransport):
        self.transport = transport

    def bootstrap(self) -> bool:
        return self.transport.bootstrap()

    def get_captcha_image(self) -> bytes | None:
        return self.transport.get_captcha_image()

    def fill_district(self, state_code: str) -> dict[str, Any] | None:
        return self._post_json("casestatus/fillDistrict", {"state_code": state_code})

    def fill_complex(self, state_code: str, dist_code: str) -> dict[str, Any] | None:
        return self._post_json(
            "casestatus/fillcomplex",
            {"state_code": state_code, "dist_code": dist_code},
        )

    def fill_court_establishment(self, state_code: str, dist_code: str, court_complex_code: str) -> dict[str, Any] | None:
        return self._post_json(
            "casestatus/fillCourtEstablishment",
            {
                "state_code": state_code,
                "dist_code": dist_code,
                "court_complex_code": court_complex_code,
            },
        )

    def fill_act_type(
        self,
        *,
        state_code: str,
        dist_code: str,
        court_complex_code: str,
        est_code: str = "",
        search_act: str = "",
    ) -> dict[str, Any] | None:
        return self._post_json(
            "casestatus/fillActType",
            {
                "state_code": state_code,
                "dist_code": dist_code,
                "court_complex_code": court_complex_code,
                "est_code": est_code,
                "search_act": search_act,
            },
        )

    def submit_act(
        self,
        *,
        state_code: str,
        dist_code: str,
        court_complex_code: str,
        est_code: str,
        actcode: str,
        section: str,
        case_status: str,
        captcha_code: str,
    ) -> dict[str, Any] | None:
        return self._post_json(
            "casestatus/submitAct",
            {
                "actcode": actcode,
                "under_sec": section,
                "case_status": case_status,
                "act_captcha_code": captcha_code,
                "state_code": state_code,
                "dist_code": dist_code,
                "court_complex_code": court_complex_code,
                "est_code": est_code,
            },
        )

    def search_by_cnr(self, *, cino: str, captcha_code: str) -> dict[str, Any] | None:
        return self._post_json(
            "cnr_status/searchByCNR/",
            {"cino": cino.upper(), "fcaptcha_code": captcha_code},
            referer_route="cnr_status/",
        )

    def view_history(
        self,
        *,
        case_no: str,
        cino: str,
        court_code: str,
        hideparty: str,
        search_flag: str,
        state_code: str,
        dist_code: str,
        court_complex_code: str,
        search_by: str,
    ) -> dict[str, Any] | None:
        return self._post_json(
            "home/viewHistory",
            {
                "court_code": court_code,
                "state_code": state_code,
                "dist_code": dist_code,
                "court_complex_code": court_complex_code,
                "case_no": case_no,
                "cino": cino,
                "hideparty": hideparty,
                "search_flag": search_flag,
                "search_by": search_by,
            },
            referer_route="casestatus/",
        )

    def view_business(self, payload: dict[str, str]) -> dict[str, Any] | None:
        data = dict(payload)
        # Keep alignment with frontend payload.
        data.setdefault("search_by", data.get("search_by", "cnr"))
        data.setdefault("state_code", payload.get("state_code", ""))
        return self._post_json("home/viewBusiness", data, referer_route="casestatus/")

    def download_pdf(self, params: dict[str, str]) -> PdfDownloadResult:
        normalized = normalize_pdf_params(params)
        if not normalized:
            return PdfDownloadResult(ok=False, content=None, source_url=None, error="invalid_pdf_params")

        ajax = self._post_json("home/display_pdf", normalized, referer_route="casestatus/")
        if ajax is not None and isinstance(ajax, dict):
            order = str(ajax.get("order") or "").strip()
            if order:
                url = order if order.startswith("http") else urljoin(f"{self.transport.base_url}/", order.lstrip("/"))
                pdf_resp = self.transport.get_absolute(
                    url,
                    headers={"Accept": "application/pdf,application/octet-stream,*/*"},
                )
                if pdf_resp and _looks_like_pdf(pdf_resp.content):
                    return PdfDownloadResult(ok=True, content=pdf_resp.content, source_url=url)
                return PdfDownloadResult(ok=False, content=None, source_url=url, error="pdf_fetch_failed")

        # Some deployments may return binary directly.
        raw = self.transport.post_route("home/display_pdf", normalized, referer_route="casestatus/")
        if raw and _looks_like_pdf(raw.content):
            return PdfDownloadResult(ok=True, content=raw.content, source_url=None)
        return PdfDownloadResult(ok=False, content=None, source_url=None, error="display_pdf_failed")

    @staticmethod
    def is_invalid_captcha(payload: dict[str, Any] | None) -> bool:
        if not payload:
            return False
        joined = " ".join(
            str(payload.get(key, ""))
            for key in ("errormsg", "casetype_list", "con", "message")
        )
        return bool(INVALID_CAPTCHA_RE.search(joined))

    def _post_json(self, route: str, data: dict[str, Any], *, referer_route: str = "casestatus/") -> dict[str, Any] | None:
        response = self.transport.post_route(route, data, referer_route=referer_route)
        if response is None:
            return None
        if isinstance(response.json_data, dict):
            return response.json_data
        parsed = _safe_json_loads(response.text)
        if isinstance(parsed, dict):
            return parsed
        return {"status": 1, "data_list": response.text}


def _safe_json_loads(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _looks_like_pdf(payload: bytes) -> bool:
    if not payload:
        return False
    idx = payload[:1024].find(b"%PDF")
    return idx != -1

