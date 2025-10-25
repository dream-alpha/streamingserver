#!/usr/bin/env python3
# Copyright (C) 2018-2025 by dream-alpha
# License: GNU General Public License v3.0 (see LICENSE file for details)

"""
Authentication Utilities

Centralized authentication methods for bypassing bot detection and capturing
authentication tokens (headers, cookies) for streaming services.
"""

from __future__ import annotations

import os
import tempfile
import subprocess
import random
from typing import Any
import requests
from debug import get_logger

try:
    import cloudscraper
except ImportError:
    cloudscraper = None

logger = get_logger(__file__)


def get_random_user_agent() -> str:
    """Generate a random realistic user agent string without creating a full AuthTokens instance."""
    # Use a closure-based lazy initialization to avoid globals
    if not hasattr(get_random_user_agent, '_cached_agents'):
        get_random_user_agent._cached_agents = [
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
            "Mozilla/5.0 (X11; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/121.0",
            "Mozilla/5.0 (X11; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/120.0",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
        ]

    return random.choice(get_random_user_agent._cached_agents)


class AuthTokens:
    """
    Container for authentication tokens and methods to acquire them.
    Supports multiple authentication methods with fallback capabilities.
    """

    def __init__(self):
        self.headers: dict[str, str] = {}
        self.cookies: dict[str, str] = {}
        self.method: str = ""
        self.last_successful_method: str | None = None
        self.session: requests.Session | None = None

    def clear(self):
        """Clear all authentication data"""
        self.headers.clear()
        self.cookies.clear()
        self.method = ""
        if self.session:
            self.session.close()
        self.session = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary format for compatibility"""
        return {
            "headers": self.headers.copy(),
            "cookies": self.cookies.copy(),
            "method": self.method
        }

    def from_dict(self, auth_dict: dict[str, Any]) -> None:
        """Load from dictionary format"""
        self.headers = auth_dict.get("headers", {}).copy()
        self.cookies = auth_dict.get("cookies", {}).copy()
        self.method = auth_dict.get("method", "")

    def get_ffmpeg_headers(self) -> str | None:
        """
        Convert authentication tokens to FFmpeg headers format.

        Returns:
            str: FFmpeg-formatted headers string, or None if no auth data
        """
        auth_tokens = self.to_dict()
        if not auth_tokens:
            return None

        headers = auth_tokens.get("headers", {})
        cookies = auth_tokens.get("cookies", {})

        if not headers and not cookies:
            return None

        # Consolidated cookie collection with deduplication
        all_cookies = {}

        # First, extract any cookies from existing Cookie headers and parse them
        for key, value in headers.items():
            if key.lower() == 'cookie' and value:
                # Parse existing Cookie header: "name1=value1; name2=value2"
                for cookie_pair in value.split(';'):
                    cookie_pair = cookie_pair.strip()
                    if '=' in cookie_pair:
                        name, cookie_value = cookie_pair.split('=', 1)
                        all_cookies[name.strip()] = cookie_value.strip()

        # Then add/override with cookies from the cookies dict (higher priority)
        if cookies:
            all_cookies.update(cookies)

        ffmpeg_headers = []

        # Add regular headers (but skip ALL Cookie headers - we'll handle cookies separately)
        for key, value in headers.items():
            if value and key.lower() != 'cookie':  # Only add non-empty values and skip Cookie headers
                ffmpeg_headers.append(f"{key}: {value}")

        # Add consolidated cookies as a single Cookie header
        if all_cookies:
            cookie_string = "; ".join([f"{name}={value}" for name, value in all_cookies.items()])
            ffmpeg_headers.append(f"Cookie: {cookie_string}")

        return "\r\n".join(ffmpeg_headers) if ffmpeg_headers else None

    def fetch_with_requests(self, url: str, domain: str | None = None) -> str | None:
        """
        Standard requests method with enhanced headers.

        Args:
            url: Target URL to fetch
            domain: Base domain for initial session establishment

        Returns:
            HTML content if successful, None otherwise
        """
        try:
            logger.info("Trying standard requests method for: %s", url)

            headers = {
                "user-agent": get_random_user_agent(),
                "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "accept-language": "en-US,en;q=0.9",
                "accept-encoding": "gzip, deflate",
                "referer": domain or "https://www.google.com/",
                "dnt": "1",
                "upgrade-insecure-requests": "1",
                "cache-control": "max-age=0",
            }

            session = requests.Session()

            # Establish session with base domain if provided
            if domain:
                session.get(domain, headers=headers, timeout=10)

            # Make request to target URL
            response = session.get(url, headers=headers, cookies=self.cookies, timeout=30)

            if response.status_code == 403:
                logger.warning("Standard requests got 403 Forbidden")
                return None

            response.raise_for_status()

            # Store auth tokens and session
            self.headers = dict(response.request.headers)
            self.cookies = session.cookies.get_dict()
            self.method = "requests"
            self.session = session  # Store the authenticated session for reuse

            logger.info("Standard requests method succeeded - captured %d cookies", len(self.cookies))
            return response.text

        except Exception as e:
            logger.error("Error with standard requests method: %s", e)
            return None

    def fetch_with_cloudscraper(self, url: str, domain: str | None = None) -> str | None:
        """
        Enhanced cloudscraper method for superior bot bypass.

        Args:
            url: Target URL to fetch
            domain: Base domain for session establishment

        Returns:
            HTML content if successful, None otherwise
        """
        if not cloudscraper:
            logger.info("Cloudscraper not available, skipping method")
            return None

        default_headers = {
            'User-Agent': get_random_user_agent(),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Referer': domain or 'https://www.google.com/',
        }

        try:
            logger.info("Trying enhanced cloudscraper method for: %s", url)
            scraper = cloudscraper.create_scraper()

            # First get base domain to establish session
            if domain:
                scraper.get(domain, timeout=10)

            # Now get the video page
            response = scraper.get(url, timeout=30)
            response.raise_for_status()

            # Capture comprehensive authentication tokens
            headers = dict(response.request.headers)
            cookies = scraper.cookies.get_dict()

            # Ensure required headers are present
            for k, v in default_headers.items():
                if k not in headers or not headers[k]:
                    headers[k] = v

            # Store comprehensive auth tokens and session
            self.headers = headers
            self.cookies = cookies
            self.method = "cloudscraper"
            self.session = scraper  # Store the authenticated session for reuse

            logger.info("Cloudscraper method succeeded - captured %d headers, %d cookies",
                        len(headers), len(cookies))
            logger.info("Cloudscraper headers: %s", headers)
            logger.info("Cloudscraper cookies: %s", cookies)

            return response.text

        except Exception as e:
            logger.error("Error with cloudscraper method: %s", e)
            return None

    def fetch_with_curl(self, url: str, domain: str | None = None) -> str | None:
        """
        Enhanced curl method with cookie jar for comprehensive auth capture.

        Args:
            url: Target URL to fetch
            domain: Base domain for session establishment

        Returns:
            HTML content if successful, None otherwise
        """
        try:
            logger.info("Trying enhanced curl method with cookie capture for: %s", url)

            # Create temporary cookie jar file
            with tempfile.NamedTemporaryFile(mode='w+', suffix='.cookies', delete=False) as cookie_jar:
                cookie_jar_path = cookie_jar.name

                # Write existing cookies to jar if we have them
                if self.cookies:
                    # Write cookies in Netscape format for curl
                    for name, value in self.cookies.items():
                        domain_name = domain.replace('https://', '').replace('http://', '') if domain else 'example.com'
                        cookie_jar.write(f"{domain_name}\tTRUE\t/\tFALSE\t0\t{name}\t{value}\n")
                    logger.info("Pre-loaded %d existing cookies into curl jar", len(self.cookies))

            try:
                # Enhanced headers to better mimic modern browser
                user_agent = get_random_user_agent()

                # Build curl command with comprehensive browser simulation
                cmd = [
                    "curl",
                    "--location",          # Follow redirects
                    "--silent",            # No progress info
                    "--max-time", "30",    # Timeout
                    "--cookie", cookie_jar_path,      # Use cookies from jar
                    "--cookie-jar", cookie_jar_path,  # Save cookies to jar
                    "--compressed",       # Accept compressed responses
                    # Comprehensive browser headers
                    "-H", f"User-Agent: {user_agent}",
                    "-H", "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                    "-H", "Accept-Language: en-US,en;q=0.9",
                    "-H", "Accept-Encoding: gzip, deflate, br",
                    "-H", "Cache-Control: max-age=0",
                    "-H", f"Referer: {domain or 'https://www.google.com/'}",
                    "-H", "sec-ch-ua: \"Chromium\";v=\"120\", \"Google Chrome\";v=\"120\", \"Not=A?Brand\";v=\"99\"",
                    "-H", "sec-ch-ua-mobile: ?0",
                    "-H", "sec-ch-ua-platform: \"Linux\"",
                    "-H", "sec-fetch-dest: document",
                    "-H", "sec-fetch-mode: navigate",
                    "-H", "sec-fetch-site: same-origin",
                    "-H", "sec-fetch-user: ?1",
                    "-H", "upgrade-insecure-requests: 1",
                    "-H", "dnt: 1",
                    url
                ]

                # First, visit base domain to establish session if provided
                if domain:
                    homepage_cmd = cmd[:-1] + [domain]
                    subprocess.run(homepage_cmd, capture_output=True, text=True, check=False, timeout=15)
                    logger.info("Curl: Established session with domain visit")

                # Now get the actual target page
                process = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=30)

                if "403 Forbidden" in process.stdout or process.returncode != 0:
                    logger.warning("Curl method failed - status: %d", process.returncode)
                    return None

                html = process.stdout

                # Read cookies back from jar and store in auth tokens
                if os.path.exists(cookie_jar_path) and os.path.getsize(cookie_jar_path) > 0:
                    try:
                        with open(cookie_jar_path, 'r', encoding='utf-8') as jar:
                            jar_content = jar.read()

                        # Parse Netscape cookie format
                        new_cookies = {}
                        for line in jar_content.strip().split('\n'):
                            if line and not line.startswith('#') and '\t' in line:
                                parts = line.split('\t')
                                if len(parts) >= 7:
                                    cookie_name = parts[5]
                                    cookie_value = parts[6]
                                    new_cookies[cookie_name] = cookie_value

                        if new_cookies:
                            # Store comprehensive auth tokens
                            self.headers = {
                                "User-Agent": user_agent,
                                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                                "Accept-Language": "en-US,en;q=0.9",
                                "Accept-Encoding": "gzip, deflate, br",
                                "Referer": domain or "https://www.google.com/",
                            }
                            self.cookies = new_cookies
                            self.method = "curl"

                            logger.info("Curl method captured %d cookies and headers", len(new_cookies))
                            logger.info("Curl captured cookies: %s", new_cookies)

                    except Exception as e:
                        logger.warning("Failed to parse cookie jar: %s", e)

                if html and len(html) > 1000:  # Check if we got meaningful content
                    logger.info("Enhanced curl method succeeded (%d bytes)", len(html))
                    return html

                logger.warning("Curl returned insufficient content (%d bytes)", len(html) if html else 0)
                return None

            finally:
                # Clean up temporary cookie jar
                try:
                    if os.path.exists(cookie_jar_path):
                        os.unlink(cookie_jar_path)
                except Exception:
                    pass  # Ignore cleanup errors

        except Exception as e:
            logger.error("Error with enhanced curl method: %s", e)
            return None

    def fetch_with_fallback(self, url: str, domain: str | None = None) -> str | None:
        """
        Try multiple authentication methods in sequence until one succeeds.

        Args:
            url: Target URL to fetch
            domain: Base domain for session establishment

        Returns:
            HTML content and method used if successful, None otherwise
        """
        logger.info("Attempting multi-method authentication for: %s", url)

        # If we have a last successful method, try it first
        if self.last_successful_method:
            logger.info("Trying previously successful method first: %s", self.last_successful_method)

            if self.last_successful_method == "requests":
                html = self.fetch_with_requests(url, domain)
            elif self.last_successful_method == "cloudscraper":
                html = self.fetch_with_cloudscraper(url, domain)
            elif self.last_successful_method == "curl":
                html = self.fetch_with_curl(url, domain)
            else:
                html = None

            if html:
                logger.info("Last successful method worked again: %s", self.last_successful_method)
                return html

        # Try all methods in sequence
        methods = [
            ("requests", self.fetch_with_requests),
            ("cloudscraper", self.fetch_with_cloudscraper),
            ("curl", self.fetch_with_curl),
        ]

        for method_name, method_func in methods:
            self.clear()  # Clear previous auth data
            html = method_func(url, domain)
            if html:
                self.last_successful_method = method_name
                logger.info("Authentication successful with method: %s", method_name)
                return html

        logger.error("All authentication methods failed for: %s", url)
        return None


def get_headers(header_type: str = "standard") -> dict[str, str]:
    """Get HTTP headers for different request types.

    Args:
        header_type: Type of headers to generate
            - "standard": Standard web request headers
            - "api": API-focused headers
            - "browser": Full browser simulation headers

    Returns:
        dict: Headers with random user agent
    """
    base_headers = {
        'User-Agent': get_random_user_agent(),
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
    }

    if header_type == "api":
        base_headers['Accept'] = '*/*'
    elif header_type == "browser":
        base_headers.update({
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Cache-Control': 'max-age=0',
        })
    else:  # "standard"
        base_headers.update({
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Upgrade-Insecure-Requests': '1',
        })

    return base_headers
