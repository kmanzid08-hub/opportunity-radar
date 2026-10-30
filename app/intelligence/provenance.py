"""Conservative, offline resolution from observed links, never URL invention/fetching."""

import ipaddress
import re
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from app.intelligence.schemas import OpportunityAnalysis, SourceRole


def public_url(value: str | None) -> str | None:
    if (
        not value
        or len(value) > 2048
        or any(ord(char) < 32 for char in value)
        or "\\" in value
    ):
        return None
    try:
        parts = urlsplit(value.strip())
        host = (parts.hostname or "").lower()
        if parts.scheme not in {"http", "https"} or parts.username or parts.password:
            return None
        if (
            not host
            or "." not in host
            or host.endswith((".local", ".internal", ".localhost"))
        ):
            return None
        if parts.port not in {None, 80, 443}:
            return None
        try:
            if not ipaddress.ip_address(host).is_global:
                return None
        except ValueError:
            pass
        if set(parse_qs(parts.query)) & {
            "api_key",
            "key",
            "token",
            "access_token",
            "auth",
        }:
            return None
        return urlunsplit(
            (parts.scheme, parts.netloc.lower(), parts.path or "/", parts.query, "")
        )
    except ValueError:
        return None


def observed_links(soup: BeautifulSoup, page_url: str) -> list[str]:
    """Capture evidence from already downloaded HTML without following any links."""
    links: list[str] = []
    for anchor in soup.select("a[href]"):
        value = public_url(urljoin(page_url, str(anchor.get("href", ""))))
        if value and value not in links:
            links.append(value)
        if len(links) >= 80:
            break
    return links


def same_domain(url: str, domain_url: str) -> bool:
    host = (urlsplit(url).hostname or "").removeprefix("www.").lower()
    authority = (urlsplit(domain_url).hostname or "").removeprefix("www.").lower()
    return bool(authority and (host == authority or host.endswith("." + authority)))


def notice_url(url: str) -> bool:
    """URL shape is a provenance hint, never an opportunity-validity decision."""
    path = urlsplit(url).path.rstrip("/").lower()
    last = path.rsplit("/", 1)[-1]
    if last in {
        "",
        "index.html",
        "jobs",
        "consultancy",
        "tenders",
        "tender",
        "procurement",
        "notices",
        "notice",
        "rfp",
        "rfq",
        "eoi",
        "tor",
        "documents",
        "opportunities",
        "careers",
        "search",
    }:
        return False
    return bool(
        re.search(
            r"(?:^|[/_-])(notices?|tenders?|rfp|rfq|eoi|tor|procurement|consultancy|request|terms|opportunities)(?:[/_.0-9-]|$)",
            path,
        )
    )


def resolve_original(
    *,
    analysis: OpportunityAnalysis,
    discovered_url: str,
    links: list[str],
    source_role: SourceRole,
    authoritative_domains: list[str],
) -> str | None:
    discovered = public_url(discovered_url)
    observed = list(
        dict.fromkeys(value for link in links if (value := public_url(link)))
    )
    domains = [
        value for domain in authoritative_domains if (value := public_url(domain))
    ]
    # Semantic issuer identification is accepted only when the declared website
    # is an actual captured link, not a URL invented by the model.
    buyer_website = public_url(analysis.buyer_website)
    if buyer_website is not None and buyer_website in observed:
        domains.append(buyer_website)
    if (
        discovered
        and source_role is SourceRole.AUTHORITATIVE
        and notice_url(discovered)
    ):
        return discovered
    if (
        discovered
        and any(same_domain(discovered, domain) for domain in domains)
        and notice_url(discovered)
    ):
        return discovered
    candidates = [
        link
        for link in observed
        if notice_url(link) and any(same_domain(link, domain) for domain in domains)
    ]
    proposed = public_url(analysis.original_source_url)
    if proposed in candidates:
        return proposed
    if (
        proposed in observed
        and proposed
        and re.search(r"\.(pdf|docx?|odt)$", urlsplit(proposed).path.lower())
        and any(same_domain(proposed, domain) for domain in domains)
    ):
        return proposed
    # Ambiguous notice links are not sufficient to choose an original.
    return candidates[0] if len(candidates) == 1 else None


def same_issuer(left: str | None, right: str | None) -> bool:
    def normalize(value: str | None) -> str:
        return re.sub(r"[^\w]", "", (value or "").casefold())

    return bool(normalize(left) and normalize(left) == normalize(right))
