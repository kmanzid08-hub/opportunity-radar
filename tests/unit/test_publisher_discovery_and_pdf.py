"""Regression: small associations with PDF tenders must be discoverable."""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from bs4 import BeautifulSoup
from sqlalchemy import select

from app import source_discovery
from app.intelligence.schemas import SourceAnalysis
from app.models import Source
from app.scanners import company_websites, pdf_text
from app.scanners.company_websites import CompanyWebsiteScanner
from app.source_discovery import SearchResult, SourceDiscovery
from app.source_quality import SourceQualityEvaluator
from app.discovery.categories import DISCOVERY_CATEGORIES


RALGA_SITE = "https://www.ralga.rw"
RALGA_ANNOUNCEMENTS = "https://www.ralga.rw/media/announcements"
RALGA_PDF = (
    "https://www.ralga.rw/fileadmin/user_upload/RALGA/Announcements/"
    "Tender_Notice-Audit_Services.pdf"
)


def _discovery(monkeypatch, *, analyzer=None, fetcher=None):
    monkeypatch.setenv("BRAVE_API_KEY", "example-not-live")
    return SourceDiscovery(
        country="Rwanda",
        source_analyzer=analyzer or (lambda **_: SourceAnalysis(
            decision="ACCEPT", confidence=93,
            reason="Official website with its own procurement notice."
        )),
        content_fetcher=fetcher or (lambda url: "RALGA public announcements with tenders."),
    )


def test_publishers_keep_announcements_and_notices_url():
    quality = SourceQualityEvaluator()
    assert quality.choose_monitor_url(RALGA_ANNOUNCEMENTS) == RALGA_ANNOUNCEMENTS
    assert quality.choose_monitor_url("https://association.example.rw/notices") == (
        "https://association.example.rw/notices"
    )
    assert quality.choose_monitor_url(
        "https://association.example.rw/about-us"
    ) == "https://association.example.rw"


def test_direct_tender_pdf_maps_to_official_publisher_site(monkeypatch):
    discovery = _discovery(monkeypatch)
    category = next(c for c in DISCOVERY_CATEGORIES if c.name == "Audit and financial advisory buyers")
    candidate = discovery._build_candidate(
        result=SearchResult(
            title="External audit tender by RALGA",
            url=RALGA_PDF,
            description="Official association procurement tender notice in Rwanda",
        ),
        query='"Rwanda" external audit accounting tender consulting',
        category=category,
    )
    assert candidate is not None
    assert candidate["monitor_url"] == RALGA_SITE
    assert candidate["domain"] == "ralga.rw"
    assert candidate["document_evidence_url"] == RALGA_PDF
    assert candidate["discovered_from"] == RALGA_PDF


def test_document_evidence_is_in_ai_prompt_and_source_is_persisted(
    monkeypatch, isolated_session_factory
):
    prompts = []
    def classify(**kwargs):
        prompts.append(kwargs["page_content"])
        return SourceAnalysis(
            decision="ACCEPT", confidence=95,
            reason="Official publisher of an audit tender PDF."
        )

    discovery = _discovery(monkeypatch, analyzer=classify)
    monkeypatch.setattr(source_discovery, "SessionLocal", isolated_session_factory)
    category = next(c for c in DISCOVERY_CATEGORIES if c.name == "Audit and financial advisory buyers")
    candidate = discovery._build_candidate(
        result=SearchResult(
            title="External audit services tender",
            url=RALGA_PDF,
            description="Official request to audit association accounts",
        ),
        query='"Rwanda" external audit accounting tender consulting',
        category=category,
    )
    assert candidate
    result = discovery._save_candidate(candidate)
    assert result.status == "ACCEPT"
    assert result.created
    assert RALGA_PDF in prompts[0]
    with isolated_session_factory() as db:
        saved = db.scalar(select(Source).where(Source.domain == "ralga.rw"))
        assert saved is not None
        assert saved.monitor_url == RALGA_SITE
        assert saved.discovered_from == RALGA_PDF
        assert saved.is_active


def test_ai_receives_actual_publication_navigation(monkeypatch):
    class Response:
        ok = True
        status_code = 200
        headers = {"Content-Type": "text/html; charset=utf-8"}
        text = """<html><body>
            <nav><a href="/media/announcements">Announcements</a>
            <a href="/about-us">About us</a></nav>
            <main>Official member association for local government.</main>
            </body></html>"""
    discovery = _discovery(monkeypatch)
    monkeypatch.setattr(discovery.content_session, "get", lambda *args, **kwargs: Response())
    content = discovery._fetch_source_content(RALGA_SITE)
    assert RALGA_ANNOUNCEMENTS in content
    assert "Official member association" in content
    assert "/about-us" not in content


def test_source_scanner_seeds_direct_pdf_when_search_first_finds_document(monkeypatch):
    scanner = CompanyWebsiteScanner()
    visited_documents = []
    monkeypatch.setattr(scanner, "_can_fetch", lambda url: True)
    monkeypatch.setattr(
        scanner, "_get_soup",
        lambda url: BeautifulSoup("<html><body>Institution homepage</body></html>", "html.parser")
    )
    monkeypatch.setattr(scanner, "_build_page_opportunity", lambda **_: None)
    monkeypatch.setattr(scanner, "_build_document_opportunity",
                        lambda **kwargs: visited_documents.append(kwargs["url"]) or None)
    monkeypatch.setattr(scanner, "_wait_between_requests", lambda: None)
    source = Source(
        domain="ralga.rw", base_url=RALGA_SITE, monitor_url=RALGA_SITE,
        organisation_name="Local Authorities Association",
        discovered_from=RALGA_PDF, source_type="NGO",
    )
    scanner._scan_source(source)
    assert RALGA_PDF in visited_documents


def test_document_text_used_for_deadline_and_scope(monkeypatch):
    scanner = CompanyWebsiteScanner()
    text = (
        "Tender notice for external audit services for financial years "
        "2026 to 2028. The eligible audit firm must provide an audit "
        "proposal and professional qualifications. "
        "Submission deadline: 20 November 2026."
    )
    monkeypatch.setattr(company_websites, "extract_pdf_text", lambda *_args, **_kw: text)
    opportunity = scanner._build_document_opportunity(
        url=RALGA_PDF,
        organisation_name="Association of Local Government",
        source_name="Local Authorities",
    )
    assert opportunity is not None
    assert "professional qualifications" in opportunity.description
    assert opportunity.deadline == date(2026, 11, 20)
    assert opportunity.source_url == RALGA_PDF


def test_bounded_pdf_parser_extracts_text_and_closes_response(monkeypatch):
    class FakeResponse:
        headers = {"Content-Type": "application/pdf", "Content-Length": "15"}
        closed = False
        def raise_for_status(self):
            return None
        def iter_content(self, chunk_size):
            yield b"%PDF-1.7\n"
            yield b"fake-contents"
        def close(self):
            self.closed = True
    response = FakeResponse()
    monkeypatch.setattr(pdf_text, "PdfReader", lambda *_args, **_kw:
                        SimpleNamespace(pages=[SimpleNamespace(extract_text=lambda: "Deadline: 20 November 2026")]))
    session = SimpleNamespace(get=lambda *_args, **_kw: response)
    extracted = pdf_text.extract_pdf_text(session, RALGA_PDF)
    assert "Deadline: 20 November 2026" in extracted
    assert response.closed


def test_oversized_pdf_is_rejected_before_download(monkeypatch):
    class HugeResponse:
        headers = {"Content-Type": "application/pdf", "Content-Length": "100000000"}
        closed = False
        def raise_for_status(self):
            return None
        def iter_content(self, chunk_size):
            raise AssertionError("Should never download an oversized document.")
        def close(self):
            self.closed = True
    response = HugeResponse()
    assert pdf_text.extract_pdf_text(
        SimpleNamespace(get=lambda *_args, **_kw: response), RALGA_PDF
    ) == ""
    assert response.closed


def test_discovery_diagnostics_records_reason_without_secrets(monkeypatch, tmp_path):
    target = tmp_path / "source-decisions.jsonl"
    monkeypatch.setenv("DISCOVERY_DIAGNOSTICS_PATH", str(target))
    d = _discovery(monkeypatch)
    d._log_diagnostic(
        event="source_assessment", result_url=RALGA_PDF,
        status="ACCEPT", reason="Publisher document verified"
    )
    content = target.read_text()
    assert RALGA_PDF in content
    assert "Publisher document verified" in content
    assert "example-not-live" not in content


def test_announcement_page_exposes_audit_pdf_to_scanner():
    html = f"""
    <html><body><main>
      <h1>Announcements</h1>
      <a href="{RALGA_PDF}">Download: Audit Services Tender Notice</a>
      <a href="/about-ralga/overview">About association</a>
    </main></body></html>
    """
    scanner = CompanyWebsiteScanner()
    urls = scanner._extract_relevant_links(
        soup=BeautifulSoup(html, "html.parser"),
        page_url=RALGA_ANNOUNCEMENTS,
        base_domain="ralga.rw",
    )
    assert RALGA_PDF in urls


def test_retry_previous_ai_failure_preserves_site_and_activates_on_success(
    monkeypatch, isolated_session_factory
):
    monkeypatch.setattr(source_discovery, "SessionLocal", isolated_session_factory)
    with isolated_session_factory() as db:
        db.add(Source(
            organisation_name="Local Authorities Association",
            base_url=RALGA_SITE, monitor_url=RALGA_SITE,
            domain="ralga.rw", source_type="NGO",
            discovered_from=RALGA_PDF,
            ai_status="ERROR", ai_reason="Gemini source analysis failed.",
            is_active=False, is_approved=False, confidence_score=50,
            priority_score=45, url_relevance_score=0,
        ))
        db.commit()
    calls = []
    def classify(**kwargs):
        calls.append(kwargs)
        return SourceAnalysis(
            decision="ACCEPT", confidence=94,
            reason="Same-domain publisher tenders are verified."
        )
    discovery = _discovery(monkeypatch, analyzer=classify)
    assert discovery._retry_failed_source_analyses(limit=4) == 1
    assert len(calls) == 1
    with isolated_session_factory() as db:
        saved = db.scalar(select(Source).where(Source.domain == "ralga.rw"))
        assert saved.ai_status == "ACCEPT"
        assert saved.is_active is True
        assert saved.is_approved is True
        assert saved.discovered_from == RALGA_PDF


def test_permanently_blocked_site_not_retried_as_ai_failure(
    monkeypatch, isolated_session_factory
):
    monkeypatch.setattr(source_discovery, "SessionLocal", isolated_session_factory)
    with isolated_session_factory() as db:
        db.add(Source(
            organisation_name="Blocked Portal",
            base_url="https://blocked.example.rw",
            monitor_url="https://blocked.example.rw",
            domain="blocked.example.rw", source_type="Unknown",
            ai_status="ERROR", ai_reason="Source content could not be fetched. BLOCKED",
            is_active=False, is_approved=False,
        ))
        db.commit()
    def classify(**kwargs):
        raise AssertionError("Blocked pages should not consume AI retries")
    discovery = _discovery(monkeypatch, analyzer=classify)
    assert discovery._retry_failed_source_analyses(limit=4) == 0


def test_transient_timeout_in_source_library_is_reconsidered(
    monkeypatch, isolated_session_factory
):
    monkeypatch.setattr(source_discovery, "SessionLocal", isolated_session_factory)
    with isolated_session_factory() as db:
        db.add(Source(
            organisation_name="Regional Association",
            base_url="https://regional.example.rw",
            monitor_url="https://regional.example.rw",
            domain="regional.example.rw", source_type="NGO",
            ai_status="ERROR",
            ai_reason="Source content could not be fetched for AI analysis. category=TIMEOUT http_status=None",
            is_active=False, is_approved=False,
        ))
        db.commit()
    evaluated = []
    def classify(**kwargs):
        evaluated.append(kwargs["url"])
        return SourceAnalysis(
            decision="ACCEPT", confidence=85,
            reason="Public tenders on the organization website."
        )
    discovery = _discovery(monkeypatch, analyzer=classify)
    assert discovery._retry_failed_source_analyses(limit=2) == 1
    assert evaluated == ["https://regional.example.rw"]


def test_permanent_forbidden_source_is_not_retried(
    monkeypatch, isolated_session_factory
):
    monkeypatch.setattr(source_discovery, "SessionLocal", isolated_session_factory)
    with isolated_session_factory() as db:
        db.add(Source(
            organisation_name="Private Buyer",
            base_url="https://access-denied.example.rw",
            monitor_url="https://access-denied.example.rw",
            domain="access-denied.example.rw",
            ai_status="ERROR",
            ai_reason="Source content could not be fetched for AI analysis. category=BLOCKED http_status=403",
            is_active=False, is_approved=False,
            source_type="Unknown",
        ))
        db.commit()
    discovery = _discovery(
        monkeypatch,
        analyzer=lambda **_: (_ for _ in ()).throw(AssertionError("Unexpected AI call")),
    )
    assert discovery._retry_failed_source_analyses(limit=2) == 0


def test_image_only_pdf_uses_ocr_only_when_explicitly_enabled(monkeypatch):
    class ImageOnlyResponse:
        headers = {"Content-Type": "application/pdf"}
        def raise_for_status(self):
            return None
        def iter_content(self, chunk_size):
            yield b"%PDF-1.7\nimage-data"
        def close(self):
            return None
    monkeypatch.setattr(pdf_text, "PdfReader", lambda *_args, **_kw:
                        SimpleNamespace(pages=[SimpleNamespace(extract_text=lambda: "")]))
    from app.scanners import pdf_ocr
    calls = []
    monkeypatch.setattr(
        pdf_ocr, "ocr_image_only_pdf",
        lambda pdf: calls.append(pdf) or "Submission deadline: 20 November 2026"
    )
    session = SimpleNamespace(get=lambda *_args, **_kw: ImageOnlyResponse())
    monkeypatch.delenv("ENABLE_PDF_OCR", raising=False)
    assert pdf_text.extract_pdf_text(session, RALGA_PDF) == ""
    assert calls == []
    monkeypatch.setenv("ENABLE_PDF_OCR", "true")
    text = pdf_text.extract_pdf_text(session, RALGA_PDF)
    assert "20 November 2026" in text
    assert len(calls) == 1


def test_ocr_result_is_reused_by_content_hash_without_repeating_ocr(monkeypatch, tmp_path):
    from app.scanners import pdf_ocr
    pdf = b"%PDF-1.7\n" + b"image-only-document"
    monkeypatch.setenv("PDF_OCR_CACHE_PATH", str(tmp_path / "ocr-cache.json"))
    monkeypatch.setattr(pdf_ocr.shutil, "which", lambda name: "/usr/bin/tesseract")
    monkeypatch.setattr(pdf_ocr, "_uncached_ocr_count", 0)
    calls = []
    monkeypatch.setattr(
        pdf_ocr, "_recognize",
        lambda *_args, **_kwargs: calls.append(1) or "External audit tender 2026."
    )
    assert pdf_ocr.ocr_image_only_pdf(pdf) == "External audit tender 2026."
    assert pdf_ocr.ocr_image_only_pdf(pdf) == "External audit tender 2026."
    assert calls == [1]
    assert (tmp_path / "ocr-cache.json").exists()


def test_ocr_is_bounded_and_skips_large_files_or_missing_engine(monkeypatch):
    from app.scanners import pdf_ocr
    monkeypatch.delenv("PDF_OCR_CACHE_PATH", raising=False)
    monkeypatch.setattr(pdf_ocr, "_uncached_ocr_count", 0)
    monkeypatch.setattr(pdf_ocr.shutil, "which", lambda name: None)
    assert pdf_ocr.ocr_image_only_pdf(b"%PDF-1.7\nsmall") == ""
    monkeypatch.setattr(pdf_ocr.shutil, "which", lambda name: "/usr/bin/tesseract")
    monkeypatch.setattr(pdf_ocr, "_uncached_ocr_count", pdf_ocr.MAX_UNCACHED_DOCS_PER_RUN)
    assert pdf_ocr.ocr_image_only_pdf(b"%PDF-1.7\nsecond") == ""
    assert pdf_ocr.ocr_image_only_pdf(
        b"%PDF-1.7\n" + b"x" * (pdf_ocr.MAX_OCR_PDF_BYTES + 1)
    ) == ""
