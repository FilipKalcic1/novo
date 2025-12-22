"""
API Gateway
Version: 10.0

Enterprise HTTP client for MobilityOne API.
DEPENDS ON: token_manager.py, config.py
"""

import asyncio
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Any, Optional
from urllib.parse import quote

import httpx

from config import get_settings
from services.token_manager import TokenManager

logger = logging.getLogger(__name__)
settings = get_settings()


class HttpMethod(Enum):
    """HTTP methods."""
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"


@dataclass
class APIResponse:
    """Structured API response."""
    success: bool
    status_code: int
    data: Any
    error_message: Optional[str] = None
    error_code: Optional[str] = None
    headers: Optional[Dict[str, str]] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        if self.success:
            return {
                "success": True,
                "data": self.data,
                "status_code": self.status_code
            }
        return {
            "success": False,
            "error": self.error_message,
            "error_code": self.error_code,
            "status_code": self.status_code
        }


class APIGateway:
    """
    Enterprise API Gateway.
    
    Features:
    - Automatic authentication
    - Retry with exponential backoff
    - Tenant header management
    - Connection pooling
    """
    
    DEFAULT_MAX_RETRIES = 2
    DEFAULT_TIMEOUT = 30.0
    RETRY_STATUS_CODES = {408, 429, 500, 502, 503, 504}
    
    def __init__(
        self,
        base_url: Optional[str] = None,
        tenant_id: Optional[str] = None,
        redis_client=None
    ):
        """
        Initialize API Gateway.
        
        Args:
            base_url: Base URL (defaults to settings)
            tenant_id: Tenant ID (defaults to settings)
            redis_client: Redis client for token caching
        """
        self.base_url = (base_url or settings.MOBILITY_API_URL).rstrip("/")
        self.tenant_id = tenant_id or settings.tenant_id
        
        self.token_manager = TokenManager(redis_client)
        
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.DEFAULT_TIMEOUT, connect=10.0),
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=100),
            follow_redirects=True
        )
        
        logger.info(f"APIGateway initialized: {self.base_url}")
    
    async def execute(
        self,
        method: HttpMethod,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        tenant_id: Optional[str] = None,
        max_retries: Optional[int] = None
    ) -> APIResponse:
        """
        Execute HTTP request.
        
        Args:
            method: HTTP method
            path: API path
            params: Query parameters
            body: Request body
            headers: Additional headers
            tenant_id: Override tenant ID
            max_retries: Override retry count
            
        Returns:
            APIResponse
        """
        url = self._build_url(path, params)
        effective_tenant = tenant_id or self.tenant_id
        retries = max_retries if max_retries is not None else self.DEFAULT_MAX_RETRIES
        
        last_error = None
        
        for attempt in range(retries + 1):
            try:
                # Get token
                token = await self.token_manager.get_token()
                
                # Build headers
                request_headers = {
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                }
                
                # CRITICAL: x-tenant header
                if effective_tenant:
                    request_headers["x-tenant"] = effective_tenant
                
                if headers:
                    request_headers.update(headers)
                
                logger.debug(f"API Request: {method.value} {path}")
                
                # Execute
                response = await self._do_request(method, url, request_headers, body)
                
                # Handle 401
                if response.status_code == 401 and attempt < retries:
                    logger.warning("401 - Refreshing token")
                    self.token_manager.invalidate()
                    await asyncio.sleep(0.5)
                    continue
                
                # Handle retryable errors
                if response.status_code in self.RETRY_STATUS_CODES and attempt < retries:
                    delay = self._calculate_backoff(attempt)
                    logger.warning(f"Retryable error {response.status_code}, delay={delay}s")
                    await asyncio.sleep(delay)
                    continue
                
                return self._parse_response(response)
                
            except httpx.TimeoutException as e:
                last_error = f"Timeout: {e}"
                if attempt < retries:
                    await asyncio.sleep(self._calculate_backoff(attempt))
                    continue
                    
            except httpx.RequestError as e:
                last_error = f"Network error: {e}"
                if attempt < retries:
                    await asyncio.sleep(self._calculate_backoff(attempt))
                    continue
                    
            except Exception as e:
                last_error = f"Error: {e}"
                logger.error(f"API call error: {e}")
                if attempt < retries:
                    await asyncio.sleep(self._calculate_backoff(attempt))
                    continue
        
        logger.error(f"All retries exhausted: {last_error}")
        return APIResponse(
            success=False,
            status_code=0,
            data=None,
            error_message=last_error or "Request failed",
            error_code="RETRY_EXHAUSTED"
        )
    
    async def _do_request(
        self,
        method: HttpMethod,
        url: str,
        headers: Dict[str, str],
        body: Optional[Dict[str, Any]]
    ) -> httpx.Response:
        """Execute raw HTTP request."""
        if method == HttpMethod.GET:
            return await self.client.get(url, headers=headers)
        elif method == HttpMethod.POST:
            return await self.client.post(url, headers=headers, json=body)
        elif method == HttpMethod.PUT:
            return await self.client.put(url, headers=headers, json=body)
        elif method == HttpMethod.PATCH:
            return await self.client.patch(url, headers=headers, json=body)
        elif method == HttpMethod.DELETE:
            return await self.client.delete(url, headers=headers)
        else:
            raise ValueError(f"Unsupported method: {method}")
    
    def _build_url(self, path: str, params: Optional[Dict[str, Any]]) -> str:
        """
        Build URL with smart detection.

        CRITICAL FIX: If path is already a complete URL (starts with http),
        don't prepend base_url - just use it as-is.
        """
        # If path is already a complete URL, use it directly
        if path.startswith("http://") or path.startswith("https://"):
            url = path
        else:
            # Relative path - prepend base_url
            if not path.startswith("/"):
                path = "/" + path
            url = f"{self.base_url}{path}"
        
        if params:
            clean = {k: v for k, v in params.items() if v is not None}
            if clean:
                parts = []
                for k, v in clean.items():
                    if k == "Filter":
                        # KLJUČNO: Ne enkodiramo '=' u '%3D' jer API to ne priznaje
                        # safe='=' ostavlja znak jednakosti 'sirovim'
                        parts.append(f"{k}={quote(str(v), safe='=')}")
                    else:
                        parts.append(f"{k}={quote(str(v), safe='')}")
                url = f"{url}?{'&'.join(parts)}"
        return url
    
    def _parse_response(self, response: httpx.Response) -> APIResponse:
        """
        Parse HTTP response with FIREWALL protection.

        MASTER PROMPT v3.1: JSON ENFORCEMENT
        - Only JSON responses allowed (Content-Type: application/json)
        - HTML responses BLOCKED (auth redirects, nginx errors)
        - User NEVER sees HTML tags or raw error codes
        """
        headers_dict = dict(response.headers)
        content_type = response.headers.get("content-type", "").lower()

        # FIREWALL GATE 1: Detect HTML response
        is_html = (
            "text/html" in content_type or
            response.text.strip().startswith("<!DOCTYPE") or
            response.text.strip().startswith("<html")
        )

        if is_html:
            logger.error(
                f"🚨 HTML LEAKAGE BLOCKED: Status={response.status_code}, "
                f"Content-Type={content_type}"
            )

            # MASTER PROMPT v3.1: User-facing clean error messages
            if response.status_code == 200:
                # Even with 200, HTML means auth redirect or wrong endpoint
                error_msg = (
                    "Trenutno ne mogu dohvatiti te podatke zbog tehničkih poteškoća sa servisom. "
                    "API je vratio UI/Login stranicu umjesto podataka."
                )
                error_code = "HTML_RESPONSE_AUTH_ERROR"
                status = 401  # Treat as auth error
            elif response.status_code == 404:
                error_msg = (
                    "Trenutno ne mogu dohvatiti te podatke zbog tehničkih poteškoća sa servisom. "
                    "Traženi resurs nije pronađen."
                )
                error_code = "NOT_FOUND"
                status = 404
            elif response.status_code == 405:
                error_msg = (
                    "Trenutno ne mogu dohvatiti te podatke zbog tehničkih poteškoća sa servisom. "
                    "Greška u konfiguraciji API zahtjeva."
                )
                error_code = "METHOD_NOT_ALLOWED"
                status = 405
            else:
                error_msg = (
                    "Trenutno ne mogu dohvatiti te podatke zbog tehničkih poteškoća sa servisom."
                )
                error_code = "HTML_RESPONSE_ERROR"
                status = response.status_code

            return APIResponse(
                success=False,
                status_code=status,
                data=None,
                error_message=error_msg,
                error_code=error_code,
                headers=headers_dict
            )

        # FIREWALL GATE 2: Normal error handling (non-HTML)
        if response.status_code >= 400:
            error_msg = self._extract_error_message(response)
            error_code = self._map_status_code(response.status_code)

            logger.warning(f"API error: {response.status_code} - {error_msg[:200]}")

            return APIResponse(
                success=False,
                status_code=response.status_code,
                data=None,
                error_message=error_msg,
                error_code=error_code,
                headers=headers_dict
            )

        # FIREWALL GATE 3: Success response - parse JSON
        try:
            data = response.json()
        except Exception as e:
            # JSON parsing failed - might be empty response or plain text
            logger.warning(f"JSON parsing failed: {e}")
            data = response.text if response.text else None

        return APIResponse(
            success=True,
            status_code=response.status_code,
            data=data,
            headers=headers_dict
        )
    
    def _extract_error_message(self, response: httpx.Response) -> str:
        """Extract error message from response."""
        try:
            data = response.json()
            for field in ["message", "error", "detail", "title", "Message", "Error"]:
                if field in data and data[field]:
                    return str(data[field])
            if isinstance(data, dict):
                return str(data)[:500]
            return response.text[:500]
        except Exception:
            return f"HTTP {response.status_code}: {response.text[:500]}"
    
    def _map_status_code(self, status: int) -> str:
        """Map status code to error code."""
        mapping = {
            400: "BAD_REQUEST",
            401: "UNAUTHORIZED",
            403: "FORBIDDEN",
            404: "NOT_FOUND",
            405: "METHOD_NOT_ALLOWED",
            422: "VALIDATION_ERROR",
            429: "RATE_LIMITED",
            500: "SERVER_ERROR",
            502: "BAD_GATEWAY",
            503: "SERVICE_UNAVAILABLE"
        }
        return mapping.get(status, f"HTTP_{status}")
    
    def _calculate_backoff(self, attempt: int) -> float:
        """Calculate exponential backoff."""
        import random
        base = 2 ** attempt
        jitter = random.uniform(0, 0.5)
        return min(base + jitter, 30)
    
    # === CONVENIENCE METHODS ===
    
    async def get(self, path: str, params: Optional[Dict] = None, **kwargs) -> APIResponse:
        """GET request."""
        return await self.execute(HttpMethod.GET, path, params=params, **kwargs)
    
    async def post(self, path: str, body: Optional[Dict] = None, **kwargs) -> APIResponse:
        """POST request."""
        return await self.execute(HttpMethod.POST, path, body=body, **kwargs)
    
    async def put(self, path: str, body: Optional[Dict] = None, **kwargs) -> APIResponse:
        """PUT request."""
        return await self.execute(HttpMethod.PUT, path, body=body, **kwargs)
    
    async def delete(self, path: str, **kwargs) -> APIResponse:
        """DELETE request."""
        return await self.execute(HttpMethod.DELETE, path, **kwargs)
    
    async def close(self) -> None:
        """Close HTTP client."""
        if self.client:
            await self.client.aclose()
            logger.info("APIGateway closed")
