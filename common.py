import logging
import os
import time
from urllib.parse import urlparse
import requests

logger = logging.getLogger(__name__)


def check_url_security(url: str) -> None:
    """Warn if an insecure HTTP endpoint is used outside localhost."""
    parsed = urlparse(url)
    if parsed.scheme.lower() == "http":
        host = (parsed.hostname or "").lower()
        if host not in ("localhost", "127.0.0.1", "::1"):
            logger.warning("Insecure HTTP endpoint detected: %s. Use HTTPS in production.", url)


def extract_records(payload: object) -> list[dict]:
    """Extract list of records from either a bare list or wrapped dictionary."""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]

    if isinstance(payload, dict):
        for key in ("data", "docs", "items", "results", "students"):
            records = payload.get(key)
            if isinstance(records, list):
                return [item for item in records if isinstance(item, dict)]

    return []


def fetch_json(
    url: str,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: int = 10,
    max_retries: int = 3,
    retry_delay: float = 1.0,
) -> dict | list:
    """
    Fetch JSON from a URL with retries, timeout, and status code handling.
    Retries on network errors and 5xx errors.
    Handles 429 using Retry-After.
    Fails fast without retry on 401/403 (auth) and 404 (not found).
    """
    check_url_security(url)

    request_headers = dict(headers or {})
    # Inject API key from environment if not already explicitly set
    api_key = os.environ.get("API_KEY")
    if api_key and "X-API-Key" not in request_headers:
        request_headers["X-API-Key"] = api_key

    for attempt in range(1, max_retries + 1):
        try:
            response = requests.get(
                url,
                params=params,
                headers=request_headers,
                timeout=timeout,
            )
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as err:
            if attempt == max_retries:
                logger.error("Network error on %s after %d attempts: %s", url, max_retries, err)
                raise
            logger.warning(
                "Network issue (%s) on attempt %d/%d. Retrying in %.1fs...",
                type(err).__name__,
                attempt,
                max_retries,
                retry_delay,
            )
            time.sleep(retry_delay)
            continue
        except requests.exceptions.RequestException as err:
            logger.error("Request failed for %s: %s", url, err)
            raise

        # Client authentication errors: stop immediately
        if response.status_code in (401, 403):
            msg = f"Authentication failed (HTTP {response.status_code}): please check your API_KEY environment variable."
            logger.error(msg)
            raise PermissionError(msg)

        # URL not found: stop immediately
        if response.status_code == 404:
            msg = f"Resource not found (HTTP 404): wrong URL '{url}'."
            logger.error(msg)
            raise FileNotFoundError(msg)

        # Rate limit handling: respect Retry-After header
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            try:
                wait_time = float(retry_after) if retry_after else 2.0
            except ValueError:
                wait_time = 2.0
            logger.warning("Rate limit reached (HTTP 429). Retrying after %.1fs...", wait_time)
            time.sleep(wait_time)
            continue

        # Server errors (5xx): retry
        if response.status_code >= 500:
            if attempt == max_retries:
                msg = f"Server error HTTP {response.status_code} after {max_retries} attempts."
                logger.error(msg)
                raise requests.HTTPError(msg, response=response)
            logger.warning(
                "Server error HTTP %d on attempt %d/%d. Retrying in %.1fs...",
                response.status_code,
                attempt,
                max_retries,
                retry_delay,
            )
            time.sleep(retry_delay)
            continue

        # Ensure other non-2xx status codes raise
        response.raise_for_status()

        # Parse and return JSON payload
        try:
            return response.json()
        except ValueError as err:
            logger.error("Invalid JSON response from %s: %s", url, err)
            raise ValueError(f"Invalid JSON received from {url}") from err

    raise RuntimeError(f"Failed to fetch data from {url} after {max_retries} attempts.")


def fetch_all_pages(
    start_url: str,
    params: dict | None = None,
    page_delay: float = 0.5,
    max_pages: int | None = None,
    page_param: str | None = None,
) -> list[dict]:
    """
    Follow pages until no more pages remain.
    Supports either link-based pagination (via 'next' in response)
    or counter-based pagination (via page_param query param).
    """
    all_records: list[dict] = []
    current_url = start_url
    current_params = dict(params or {})
    current_page = 1

    while current_url:
        if page_param:
            current_params[page_param] = current_page

        logger.info("Fetching: %s (page %d)", current_url, current_page)
        payload = fetch_json(current_url, params=current_params)
        records = extract_records(payload)
        all_records.extend(records)

        # Link-based pagination (e.g. mock API returning "next")
        next_url = None
        if isinstance(payload, dict):
            next_url = payload.get("next")

        if next_url:
            current_url = next_url
            current_params = {}
        elif page_param:
            # If records returned are empty, no more pages
            if not records:
                break
        else:
            # Single page response
            break

        current_page += 1
        if max_pages is not None and current_page > max_pages:
            break

        # Pause to avoid hammering the endpoint
        time.sleep(page_delay)

    return all_records
