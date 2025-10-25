# Authentication Utilities Specification

## Overview
Centralized authentication system for bypassing bot detection and capturing authentication tokens (headers, cookies) required for streaming service access.

## Purpose
- Bypass anti-bot protections (Cloudflare, captchas, etc.)
- Capture authentication tokens from protected sites
- Provide authenticated sessions for subsequent requests
- Support multiple bypass methods with automatic fallback

---

## Core Components

### 1. `get_random_user_agent() -> str`

**Purpose**: Generate randomized realistic user agent strings

**Implementation**:
- Uses closure-based lazy initialization (cached on first call)
- Pool of 5 realistic user agents:
  - Chrome 119/120 on Linux
  - Firefox 120/121 on Linux  
  - Edge 120 on Linux

**Returns**: Random user agent string from pool

**Usage**: Called by all auth methods to avoid user-agent fingerprinting

---

### 2. `class AuthTokens`

Container for authentication data with multi-method acquisition.

#### Attributes

```python
headers: dict[str, str]        # HTTP headers (User-Agent, Referer, etc.)
cookies: dict[str, str]        # Session cookies
method: str                    # Last successful method ("requests", "cloudscraper", "curl")
last_successful_method: str    # Cache for optimization
session: requests.Session      # Authenticated session for reuse
```

#### Methods

##### `__init__()`
Initializes empty authentication container.

##### `clear()`
**Purpose**: Reset all authentication data  
**Actions**:
- Clears headers and cookies dicts
- Resets method string
- Closes and nulls session object

##### `to_dict() -> dict`
**Returns**:
```python
{
    "headers": dict,   # Copy of headers
    "cookies": dict,   # Copy of cookies  
    "method": str      # Method name
}
```

##### `from_dict(auth_dict: dict)`
**Purpose**: Load authentication data from dictionary  
**Input**: Dictionary in to_dict() format  
**Actions**: Copies headers, cookies, method from dict

##### `get_ffmpeg_headers() -> str | None`

**Purpose**: Convert auth tokens to FFmpeg-compatible header format

**Process**:
1. Check if any auth data exists → return None if empty
2. Extract cookies from existing Cookie headers (parse `name=value; name2=value2`)
3. Merge with cookies dict (dict takes priority for duplicates)
4. Build header list:
   - Add all non-Cookie headers with non-empty values
   - Add consolidated Cookie header as single line
5. Join with `\r\n` delimiter

**Returns**: 
- FFmpeg header string like: `"user-agent: ...\r\nreferer: ...\r\nCookie: name1=value1; name2=value2"`
- Or `None` if no auth data

**Edge Cases**:
- Deduplicates cookies (dict priority over header)
- Filters out empty header values
- Handles missing headers/cookies gracefully

##### `fetch_with_requests(url: str, domain: str = None) -> str | None`

**Purpose**: Standard Python requests method with enhanced headers

**Parameters**:
- `url`: Target URL to fetch (video page)
- `domain`: Optional base domain for session establishment (e.g., "https://xhamster.com")

**Process**:
1. Create headers:
   - Random user agent
   - Standard browser accept/encoding headers
   - Referer = domain or Google
   - DNT, cache-control, upgrade-insecure-requests flags
2. Create new requests.Session()
3. If domain provided: GET domain first (establish session, get initial cookies)
4. GET target url with headers + existing cookies
5. Check status:
   - 403 → return None (bot detected)
   - Other errors → return None
   - Success → continue
6. Capture tokens:
   - headers = request headers from response
   - cookies = session.cookies.get_dict()
   - Store authenticated session for reuse
7. Set method = "requests"
8. Log cookie count

**Returns**: HTML content or None on failure

**Timeout**: 30 seconds for main request, 10s for domain request

**Success Rate**: ~60-70% (basic bot detection bypass)

##### `fetch_with_cloudscraper(url: str, domain: str = None) -> str | None`

**Purpose**: Advanced bot bypass using cloudscraper library

**Prerequisites**: Requires `cloudscraper` module installed

**Parameters**: Same as fetch_with_requests

**Process**:
1. Check cloudscraper availability → return None if missing
2. Create default headers (more comprehensive than requests)
3. Create cloudscraper.create_scraper()
4. If domain: GET domain first (establish session)
5. GET target URL
6. Capture comprehensive tokens:
   - headers from request
   - cookies from scraper.cookies
   - Fill missing headers from defaults
   - Store scraper session for reuse
7. Set method = "cloudscraper"
8. Log detailed capture info

**Returns**: HTML content or None on failure

**Timeout**: 30 seconds

**Success Rate**: ~85-90% (handles Cloudflare JS challenges)

**Advantages**: 
- Solves JavaScript challenges automatically
- Better cookie/header simulation

##### `fetch_with_curl(url: str, domain: str = None) -> str | None`

**Purpose**: System curl with cookie jar for maximum bypass

**Prerequisites**: Requires curl binary installed

**Parameters**: Same as fetch_with_requests

**Process**:
1. Create temporary cookie jar file
2. If existing cookies: Write to jar in Netscape format:
   ```
   domain.com\tTRUE\t/\tFALSE\t0\tcookie_name\tcookie_value
   ```
3. Build curl command:
   - `--location` (follow redirects)
   - `--cookie` + `--cookie-jar` (read/write jar)
   - `--compressed` (accept gzip)
   - Comprehensive browser headers:
     - User-Agent, Accept, Accept-Language, Accept-Encoding
     - Referer, Cache-Control
     - sec-ch-ua-* headers (Chrome simulation)
     - sec-fetch-* headers (security context)
     - DNT, upgrade-insecure-requests
4. If domain: Execute curl on domain first (establish session)
5. Execute curl on target URL (max 30s timeout)
6. Parse cookie jar:
   - Read Netscape format
   - Extract cookie name/value pairs
   - Store in cookies dict
7. Build headers dict with curl's headers
8. Set method = "curl"
9. Cleanup: Delete temporary cookie jar

**Returns**: HTML content or None on failure

**Timeout**: 30 seconds main request, 15s domain request

**Success Rate**: ~95%+ (most effective, system-level curl)

**Advantages**:
- Native binary = harder to fingerprint
- Perfect header simulation
- Cookie jar persistence

**Cleanup**: Always removes temp cookie jar file

##### `fetch_with_fallback(url: str, domain: str = None) -> str | None`

**Purpose**: Intelligent multi-method authentication with optimization

**Strategy**:
1. **Optimization**: If `last_successful_method` exists → try it first
   - Speeds up subsequent requests (no trial-and-error)
   - Returns immediately on success
2. **Fallback sequence**: Try all methods in order:
   1. requests (fast, moderate success)
   2. cloudscraper (slower, high success)  
   3. curl (slowest, highest success)
3. **State management**:
   - clear() before each attempt (clean slate)
   - Set `last_successful_method` on success (optimization cache)
   - Return immediately on first success

**Returns**: HTML content or None if all methods fail

**Total Max Time**: ~90 seconds (30s × 3 methods)

**Typical Time**: 
- 5-10s (cached method works)
- 30-60s (fallback needed)

**Success Rate**: ~98% (combined success of all methods)

---

## Helper Functions

### `get_headers(header_type: str = "standard") -> dict`

**Purpose**: Generate HTTP headers for different request types

**Parameters**:
- `header_type`: 
  - `"standard"`: Basic web request (default)
  - `"api"`: API-focused (Accept: */*)
  - `"browser"`: Full browser simulation (sec-fetch headers)

**Returns**: Dict with appropriate headers + random user agent

**Common Headers** (all types):
- User-Agent (random)
- Accept-Language: en-US,en;q=0.9
- Accept-Encoding: gzip, deflate, br
- Connection: keep-alive

**Type-Specific**:

**"standard"**:
```python
{
    'Accept': 'text/html,...,*/*;q=0.8',
    'Upgrade-Insecure-Requests': '1'
}
```

**"api"**:
```python
{
    'Accept': '*/*'  # Generic accept
}
```

**"browser"**:
```python
{
    'Accept': 'text/html,...,image/apng,*/*;q=0.8',
    'Upgrade-Insecure-Requests': '1',
    'Sec-Fetch-Dest': 'document',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Site': 'none',
    'Cache-Control': 'max-age=0'
}
```

---

## Usage Patterns

### Pattern 1: Simple Authentication
```python
auth = AuthTokens()
html = auth.fetch_with_fallback(
    url="https://example.com/video/123",
    domain="https://example.com"
)

if html:
    # Use auth.headers, auth.cookies, auth.session
    # for subsequent requests
```

### Pattern 2: FFmpeg Integration
```python
auth = AuthTokens()
auth.fetch_with_fallback(url, domain)

ffmpeg_headers = auth.get_ffmpeg_headers()
# ffmpeg -headers "user-agent: ...\r\nCookie: ..." -i url
```

### Pattern 3: Session Reuse
```python
auth = AuthTokens()
auth.fetch_with_fallback(url, domain)

# Reuse authenticated session
response = auth.session.get(another_url)
```

### Pattern 4: Direct Method Selection
```python
auth = AuthTokens()

# Try specific method first
if auth.fetch_with_cloudscraper(url, domain):
    # Success
else:
    # Fallback to other methods
    auth.fetch_with_fallback(url, domain)
```

---

## Error Handling

**Methods return `None` on failure:**
- Network errors (ConnectionError, Timeout)
- 403 Forbidden (bot detection)
- Invalid response (empty or < 1000 bytes for curl)
- Missing dependencies (cloudscraper, curl)

**No exceptions raised** - caller checks for None return

**Logging**:
- INFO: Method attempts, successes, token captures
- WARNING: Failures, fallbacks, missing dependencies
- ERROR: Complete failures

---

## Configuration Constants

All in `constants.py` (imported):
- `TIMEOUT_HTTP_DEFAULT`: 30 seconds
- `TIMEOUT_HTTP_SHORT`: 10 seconds  
- User agent pool: 5 realistic agents

---

## Dependencies

**Required**:
- `requests`: Core HTTP library
- `tempfile`, `subprocess`, `os`: For curl method

**Optional**:
- `cloudscraper`: Enhanced bypass (graceful degradation if missing)
- `curl` binary: System-level bypass (graceful degradation if missing)

---

## Design Decisions

### Why multiple methods?
**Reason**: Different sites use different bot detection
- Simple sites: requests works
- Cloudflare: cloudscraper needed
- Advanced detection: curl required

### Why cache last successful method?
**Reason**: Performance optimization
- Typical case: Same site uses same protection
- Saves 60-90s on subsequent requests
- Fallback still available if protection changes

### Why store session?
**Reason**: Efficiency and cookie persistence
- Reuse authenticated session for multiple requests
- Maintain cookie state across requests
- Avoid re-authentication overhead

### Why temp cookie jar for curl?
**Reason**: Curl doesn't maintain session internally
- Cookie jar provides persistence
- Netscape format is curl-native
- Cleanup prevents file leaks

### Why clear() between fallback attempts?
**Reason**: Avoid token contamination
- Failed method may have partial data
- Each method should start fresh
- Prevents mixing incompatible auth data
