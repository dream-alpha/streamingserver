# Session Handling Specification

## Overview
Session management system for maintaining HTTP state across requests to streaming services. Sessions preserve cookies, headers, and connection pools for efficient authenticated communication.

## Purpose
- Maintain HTTP state (cookies, headers) across requests
- Reuse authenticated sessions from resolvers to recorders
- Provide connection pooling for performance
- Enable proper browser simulation through consistent headers

---

## Core Components

### 1. `get_session() -> requests.Session`

**Purpose**: Create a new HTTP session with browser-like headers

**Location**: `session_utils.py`

**Implementation**:
```python
def get_session():
    session = requests.Session()
    session.headers.update(get_headers("browser"))
    return session
```

**Process**:
1. Create new `requests.Session` object
2. Apply "browser" type headers from `get_headers()`
3. Return configured session

**Default Headers Applied** (from `get_headers("browser")`):
```python
{
    'User-Agent': '<random from pool>',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
    'Sec-Fetch-Dest': 'document',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Site': 'none',
    'Cache-Control': 'max-age=0'
}
```

**Returns**: Configured `requests.Session` object

**Usage Pattern**:
```python
session = get_session()
response = session.get(url)
# Session maintains cookies and connection pool
```

---

## Session Lifecycle

### Phase 1: Creation in Resolver

**BaseResolver.__init__**:
```python
def __init__(self):
    self.session = get_session()
    self.session.headers.update(get_headers("standard"))
```

**State**:
- Fresh session with browser headers
- No cookies yet
- No authentication

### Phase 2: Authentication (for protected content)

**Pattern A: Auth via AuthTokens** (xHamster, XVideos, XNXX):
```python
# In resolver.resolve_url()
self.auth_tokens = AuthTokens()
html = self.auth_tokens.fetch_with_fallback(url, domain)

# Auth methods create authenticated session internally:
# - fetch_with_requests() → self.session = requests.Session()
# - fetch_with_cloudscraper() → self.session = cloudscraper.create_scraper()
# - fetch_with_curl() → None (no persistent session)
```

**Result**: `self.auth_tokens.session` contains authenticated session with captured cookies

**Pattern B: Direct Session Use** (PlutoTV, SamsungTV):
```python
# In resolver.resolve_url()
response = self.session.get(url)
# Session automatically captures cookies from response
```

**Result**: `self.session` contains cookies from direct requests

### Phase 3: Session Transfer to Recorder

**Resolver returns session in resolve_result**:

**Pattern A (AuthTokens-based)**:
```python
return {
    "resolved_url": video_url,
    "auth_tokens": self.auth_tokens.to_dict(),
    "session": self.auth_tokens.session,  # Authenticated session
    "recorder_id": "mp4",
    # ...
}
```

**Pattern B (Direct session)**:
```python
return {
    "resolved_url": video_url,
    "session": self.session,  # Session with captured cookies
    "recorder_id": "hls_basic",
    # ...
}
```

### Phase 4: Session Use in Recorder

**MP4_Recorder.record_start()**:
```python
def record_start(self, resolve_result):
    self.session = resolve_result.get("session")
    
    if not self.session:
        # Fallback: create basic session
        self.session = requests.Session()
```

**MP4_Recorder.record_stream()**:
```python
def record_stream(self, url, rec_dir):
    session = self.session
    
    # Use authenticated session for download
    with session.get(url, stream=True) as response:
        # Download chunks...
```

**State**:
- Session contains all authentication cookies
- Headers preserve authentication context
- Connection pool reuses TCP connections

---

## Session State Management

### What Sessions Preserve

**1. Cookies**:
```python
session.cookies  # CookieJar with all cookies
session.cookies.get_dict()  # Dict format
```

**Example**:
```python
{
    '_cfg': 'abc123...',
    'x_content_preference_index': 'straight',
    'settings': 'eyJ...'
}
```

**2. Headers**:
```python
session.headers  # CaseInsensitiveDict
```

**Example**:
```python
{
    'User-Agent': 'Mozilla/5.0...',
    'Referer': 'https://xhamster.com/',
    'Origin': 'https://xhamster.com',
    # ...
}
```

**3. Connection Pool**:
- Reuses TCP connections (HTTP Keep-Alive)
- One connection per host
- Automatic connection management

### Session Modification

**Adding Headers**:
```python
session.headers.update({
    'Referer': 'https://example.com/',
    'Origin': 'https://example.com'
})
```

**Removing Headers** (e.g., Cookie deduplication):
```python
# In xHamster resolver
session.headers.pop('Cookie', None)  # Remove from headers
session.headers.pop('cookie', None)  # Case variation
# Cookies managed via session.cookies instead
```

**Adding Cookies**:
```python
session.cookies.set('name', 'value', domain='.example.com')
```

### Session Cleanup

**Manual Cleanup**:
```python
if self.session:
    self.session.close()
    self.session = None
```

**Automatic Cleanup**:
- Sessions auto-close when garbage collected
- Connection pool releases resources

---

## Session vs AuthTokens Integration

### Relationship

**AuthTokens** contains session as attribute:
```python
class AuthTokens:
    def __init__(self):
        self.session: requests.Session | None = None
```

**Session Creation by Auth Method**:

1. **fetch_with_requests()**:
   ```python
   session = requests.Session()
   # ... perform requests ...
   self.session = session  # Store for reuse
   ```

2. **fetch_with_cloudscraper()**:
   ```python
   scraper = cloudscraper.create_scraper()
   # ... perform requests ...
   self.session = scraper  # Store for reuse
   ```

3. **fetch_with_curl()**:
   ```python
   # No persistent session (subprocess-based)
   # Only captures cookies/headers as dict
   self.session = None
   ```

### Session Reuse Pattern

**Within Resolver**:
```python
# First request (authentication)
self.auth_tokens.fetch_with_fallback(page_url, domain)

# Subsequent requests reuse authenticated session
if self.auth_tokens.session:
    response = self.auth_tokens.session.get(api_url)
```

**Across Resolver → Recorder**:
```python
# Resolver
return {
    "session": self.auth_tokens.session,  # Pass session
    # ...
}

# Recorder
self.session = resolve_result.get("session")
response = self.session.get(video_url)  # Reuses cookies/auth
```

---

## Usage Patterns

### Pattern 1: Basic Session Creation
```python
from session_utils import get_session

session = get_session()
response = session.get("https://example.com")
```

### Pattern 2: Authenticated Session via AuthTokens
```python
from auth_utils import AuthTokens

auth = AuthTokens()
auth.fetch_with_fallback(url, domain)

# Reuse authenticated session
if auth.session:
    response = auth.session.get(another_url)
```

### Pattern 3: Session Transfer (Resolver → Recorder)
```python
# In Resolver
return {
    "session": self.auth_tokens.session,
    # ...
}

# In Recorder
self.session = resolve_result.get("session")
if not self.session:
    self.session = requests.Session()  # Fallback
```

### Pattern 4: Session Header Updates
```python
session = get_session()

# Update specific headers
session.headers.update({
    'Referer': 'https://site.com/',
    'X-Custom': 'value'
})

# Remove headers
session.headers.pop('Cookie', None)
```

### Pattern 5: Debug Session State
```python
# Check headers
logger.info("Headers: %s", dict(session.headers))

# Check cookies
logger.info("Cookies: %s", dict(session.cookies))

# Check cookie jar details
if hasattr(session.cookies, '_cookies'):
    logger.info("Cookie jar: %s", session.cookies._cookies)
```

---

## Error Handling

### Missing Session
```python
if not self.session:
    logger.warning("No session - creating fallback")
    self.session = requests.Session()
```

### Session Validation
```python
if not session:
    raise ValueError("No session available for downloading")
```

### No Exceptions on Session Operations
- Session methods may raise `requests.RequestException`
- Caller responsible for exception handling
- Always check `if session:` before use

---

## Performance Characteristics

### Benefits

**1. Connection Pooling**:
- Reuses TCP connections (HTTP Keep-Alive)
- ~50-100ms saved per request (no TCP handshake)
- Significant for multiple requests

**2. Cookie Persistence**:
- Automatic cookie management
- No manual cookie header building
- Correct domain/path scoping

**3. Header Consistency**:
- Headers set once, applied to all requests
- Reduces code duplication
- Easier to update authentication

### Costs

**1. Memory**:
- ~10-50 KB per session (cookies, headers, connection pool)
- Negligible for typical use

**2. Cleanup**:
- Sessions must be closed or garbage collected
- Connection pool holds resources until closed

---

## Common Pitfalls

### Pitfall 1: Cookie Header Duplication
**Problem**: Cookies in both `session.headers['Cookie']` and `session.cookies`

**Solution**:
```python
# Remove Cookie header, use session.cookies only
session.headers.pop('Cookie', None)
session.headers.pop('cookie', None)
# Cookies automatically added from session.cookies
```

### Pitfall 2: Lost Authentication
**Problem**: Creating new session loses cookies

**Solution**:
```python
# DON'T: Creates new session, loses cookies
self.session = requests.Session()

# DO: Reuse authenticated session
self.session = resolve_result.get("session")
```

### Pitfall 3: Curl Session Mismatch
**Problem**: `fetch_with_curl()` doesn't create persistent session

**Solution**:
```python
# Check if session exists before reuse
if self.auth_tokens.session:
    response = self.auth_tokens.session.get(url)
else:
    # Fall back to direct request with captured cookies
    response = requests.get(url, 
                           headers=self.auth_tokens.headers,
                           cookies=self.auth_tokens.cookies)
```

### Pitfall 4: Session Timeout
**Problem**: Session connections timeout with Keep-Alive

**Solution**:
```python
# Sessions handle this automatically via connection pool
# Expired connections are reopened transparently
response = session.get(url, timeout=30)  # Just set timeout
```

---

## Design Decisions

### Why use sessions instead of standalone requests?
**Reason**: State preservation
- Cookies persist across requests (authentication)
- Connection pooling improves performance
- Consistent header application

### Why pass session from resolver to recorder?
**Reason**: Efficiency and authentication
- Resolver already authenticated with site
- Recorder needs same authentication for video URL
- Avoids re-authentication overhead

### Why fallback to basic session in recorder?
**Reason**: Robustness
- Some sources don't need authentication
- Resolver may fail to provide session
- Basic session still works for public content

### Why browser headers in get_session()?
**Reason**: Maximum compatibility
- Sites expect browser-like headers
- Reduces bot detection risk
- Works with most streaming services

### Why not global session pool?
**Reason**: Isolation
- Each resolver needs independent state
- Prevents cookie/header contamination
- Easier debugging and testing
