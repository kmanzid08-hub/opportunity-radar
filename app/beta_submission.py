"""Bounded user submission orchestration using the existing intelligence engines."""

from hashlib import sha256
from urllib.parse import urlsplit, urlunsplit

import requests
from requests.adapters import HTTPAdapter

from app.controlled_network import BoundedCrawler, BoundedWeb
from app.beta_scope import principal, private_submission
from app.intelligence import matching_engine, opportunity_pipeline, triage_engine
from app.models import Opportunity
from app.schemas import RawOpportunity
from app import database, scanner


class PinnedAdapter(HTTPAdapter):
    """Keep TLS hostname verification while connecting to the validated public IP."""

    def __init__(self, hostname: str):
        self.hostname = hostname
        super().__init__(max_retries=0)

    def init_poolmanager(self, connections, maxsize, block=False, **kwargs):
        kwargs.update(assert_hostname=self.hostname, server_hostname=self.hostname)
        return super().init_poolmanager(connections, maxsize, block=block, **kwargs)


class PinnedSession(requests.Session):
    """The shared bounded fetcher calls this after its normal safety validation."""

    def get(self, url, **kwargs):
        import socket
        from ipaddress import ip_address
        from app.source_fetch_diagnostics import SourceFetchError

        BoundedWeb.validate_url(url)
        parsed = urlsplit(url)
        addresses = socket.getaddrinfo(
            parsed.hostname,
            parsed.port or (443 if parsed.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
        if not addresses or any(
            not ip_address(item[4][0]).is_global for item in addresses
        ):
            raise SourceFetchError("BLOCKED")
        address = addresses[0][4][0]
        authority = f"[{address}]" if ":" in address else address
        if parsed.port:
            authority += f":{parsed.port}"
        pinned = urlunsplit((parsed.scheme, authority, parsed.path, parsed.query, ""))
        if parsed.scheme == "https":
            self.mount("https://", PinnedAdapter(parsed.hostname))
        headers = dict(kwargs.pop("headers", {}), Host=parsed.netloc)
        return super().get(pinned, headers=headers, **kwargs)


def fetch_notice(url: str) -> RawOpportunity:
    """Same robots, no-redirect, content, size and time safeguards as controlled tests."""
    web = BoundedWeb(1, 1)
    web.session.close()
    web.session = PinnedSession()
    web.session.trust_env = False
    web.session.headers["User-Agent"] = BoundedCrawler.USER_AGENT
    crawler = BoundedCrawler(web, 1)
    try:
        web.validate_url(url)
        if not crawler._can_fetch(url):
            raise ValueError("The site does not allow this page to be fetched.")
        soup = crawler._get_soup(url)
        raw = crawler._build_page_opportunity(
            soup, url, None, urlsplit(url).hostname or "Submitted notice"
        )
        if raw is None:
            raise ValueError("No readable notice was found.")
        return raw
    finally:
        web.session.close()


def submit_notice(company_id: int, url: str, text: str):
    user = principal.get()
    if user is None:
        raise ValueError("Sign in to analyze an opportunity.")
    profiles = matching_engine.load_active_company_profiles([company_id])
    if len(profiles) != 1:
        raise ValueError("Create a company profile before analyzing an opportunity.")
    if text:
        raw = RawOpportunity(
            None,
            text.splitlines()[0][:200],
            "Submitted notice",
            url or "",
            description=text,
        )
    else:
        raw = fetch_notice(url)
    return process_raw_notice(company_id, raw, url, text)


def process_raw_notice(company_id: int, raw: RawOpportunity, url: str, text: str):
    user = principal.get()
    if user is None:
        raise ValueError("Sign in to analyze an opportunity.")
    profiles = matching_engine.load_active_company_profiles([company_id])
    if len(profiles) != 1:
        raise ValueError("Company not found.")
    # Private source/cache keys prevent a pasted notice from overwriting global
    # discoveries or another workspace's submission. The real URL remains usable.
    raw.source_url = (
        url + f"#radar-workspace-{user.workspace_id}"
        if url
        else f"urn:radar:workspace:{user.workspace_id}:notice:{sha256(text.encode()).hexdigest()}"
    )
    scope = private_submission.set(True)
    try:
        processed = opportunity_pipeline.process_opportunity_candidate(raw)
        if (
            processed.status
            is not opportunity_pipeline.OpportunityProcessingStatus.VERIFIED
        ):
            return None, processed.status.value
        oid = processed.duplicate_opportunity_id
        if oid is None:
            if processed.filtered_opportunity is None:
                raise ValueError("Verified notice data is unavailable.")
            scanner.save_opportunity(processed.filtered_opportunity)
            oid = opportunity_pipeline.link_analysis_to_saved_opportunity(
                raw.source_url
            )
        if oid is None:
            raise ValueError("The notice could not be saved. Please retry.")
        matches = matching_engine.match_verified_opportunity(oid, profiles=profiles)
        triage_engine.triage_verified_opportunity(
            oid, profiles=profiles, matches=matches
        )
        return oid, "VERIFIED"
    finally:
        private_submission.reset(scope)


def refresh_match(opportunity_id: int, company_id: int) -> None:
    with database.SessionLocal() as db:
        if db.get(Opportunity, opportunity_id) is None:
            raise ValueError("Opportunity not found.")
    profiles = matching_engine.load_active_company_profiles([company_id])
    if len(profiles) != 1:
        raise ValueError("Company not found.")
    matches = matching_engine.match_verified_opportunity(
        opportunity_id, profiles=profiles
    )
    triage_engine.triage_verified_opportunity(
        opportunity_id, profiles=profiles, matches=matches
    )


def fetch_source_candidates(source) -> list[RawOpportunity]:
    """One selected source, at most three pages, reusing existing link discovery."""
    web = BoundedWeb(1, 3)
    web.session.close()
    web.session = PinnedSession()
    web.session.trust_env = False
    web.session.headers["User-Agent"] = BoundedCrawler.USER_AGENT
    crawler = BoundedCrawler(web, 3)
    queue = [source.monitor_url]
    results: list[RawOpportunity] = []
    try:
        for url in queue:
            if len(crawler.considered) >= 3:
                break
            try:
                web.validate_url(url)
                if not crawler._can_fetch(url):
                    continue
                soup = crawler._get_soup(url)
                raw = crawler._build_page_opportunity(
                    soup,
                    url,
                    source.organisation_name,
                    source.organisation_name or source.domain,
                )
                if raw:
                    results.append(raw)
                if url == source.monitor_url:
                    for link in crawler._extract_relevant_links(
                        soup, url, source.domain
                    ):
                        if link not in queue and len(queue) < 3:
                            queue.append(link)
            except (requests.RequestException, ValueError, OSError):
                continue
        return results
    finally:
        web.session.close()
