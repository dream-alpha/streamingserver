# Provider System Specification

## Overview
Plugin-based provider architecture for integrating streaming services into the server. Each provider implements content discovery (categories, search) and URL resolution for recording.

## Purpose
- Enable modular addition of new streaming services
- Separate content discovery (Provider) from URL resolution (Resolver)
- Provide consistent interface for socket server communication
- Support category browsing, search, and direct URL recording

---

## Architecture

### Component Separation

**Provider**: Content discovery and metadata
- Get categories
- Get media items (videos) from categories
- Search videos
- Extract video metadata

**Resolver**: URL resolution for recording
- Convert video page URL to streamable URL
- Handle authentication and anti-bot protection
- Determine recorder type (MP4, HLS, etc.)
- Configure session with proper headers/cookies

**Rationale**: Separation allows:
- Provider to run without authentication (browsing content)
- Resolver to handle complex auth only when recording
- Independent updates to discovery vs resolution logic

---

## Directory Structure

```
src/streamingserver/providers/
├── __init__.py
├── Makefile.am
├── TEMPLATE_PROVIDER/          # Template for creating new providers
│   ├── __init__.py
│   ├── config.json             # Provider metadata
│   ├── instructions.txt        # Creation guide
│   ├── provider.py             # Provider implementation
│   └── resolver.py             # Resolver implementation
├── PlutoTV/                    # Example: Simple live TV provider
│   ├── __init__.py
│   ├── config.json
│   ├── provider.py
│   └── resolver.py
├── xHamster/                   # Example: Complex provider with auth
│   ├── __init__.py
│   ├── config.json
│   ├── provider.py
│   ├── resolver.py
│   ├── category.py             # Optional: category manager
│   └── video.py                # Optional: video manager
└── ... (other providers)
```

---

## Configuration File: `config.json`

### Purpose
Provides provider metadata for UI display and provider selection.

### Schema
```json
{
  "title": "Provider Display Name",
  "thumbnail": "logo_filename.png",
  "description": "Brief description of provider"
}
```

### Example (xHamster)
```json
{
  "title": "xHamster",
  "thumbnail": "xhamster_logo.png",
  "description": "Adult video streaming service"
}
```

### Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `title` | string | Yes | Display name shown in UI |
| `thumbnail` | string | No | Logo filename (stored in data directory) |
| `description` | string | No | Brief provider description |

### Notes
- `provider_id` automatically set from directory name by ProviderManager
- Config loaded by `ProviderManager._load_provider_config()`
- Missing config.json → default config generated from directory name

---

## Provider Implementation: `provider.py`

### Base Class: `BaseProvider`

**Location**: `base_provider.py`

**Purpose**: Common functionality for all providers

**Key Attributes**:
```python
self.provider_id: str           # Unique provider identifier (directory name)
self.data_dir: Path             # Provider data directory
self.name: str                  # Provider name
self.title: str                 # Display title
self.base_url: str              # Provider base URL
self.description: str           # Description
self.supports_categories: bool  # Whether provider has categories
self.supports_search: bool      # Whether provider supports search
self.session: requests.Session  # HTTP session for requests
```

**Key Methods**:
```python
def get_categories(self) -> list[dict[str, str]]
def get_latest_videos(self, page: int = 1, limit: int = 28) -> dict[str, Any]
def search_videos(self, term: str, page: int = 1, limit: int = 28) -> dict[str, Any]
def resolve_video_url(self, video_url: str) -> dict[str, Any]
def _clean_text(self, text: str) -> str
def parse_iso8601(self, dtstr: str) -> datetime.datetime
def get_standard_headers(self, purpose: str = "general") -> dict[str, str]
def get_response_text(self, response) -> str
def extract_video_id(self, url: str) -> str
def sanitize_for_json(self, text: str) -> str
```

### Required Provider Class

**Pattern**: Every provider must implement `class Provider(BaseProvider)`

**Minimal Implementation**:
```python
from base_provider import BaseProvider
from session_utils import get_session

class Provider(BaseProvider):
    def __init__(self, provider_id, data_dir):
        super().__init__(provider_id, data_dir)
        self.base_url = "https://example.com"
        self.name = "Example"
        self.supports_categories = True
        self.supports_search = True
        
    def get_categories(self) -> list[dict[str, str]]:
        """Return list of category dicts"""
        return [
            {"name": "Category 1", "url": "https://example.com/cat1", "icon": "cat1.png"},
            {"name": "Category 2", "url": "https://example.com/cat2", "icon": "cat2.png"}
        ]
    
    def get_media_items(self, category: dict, page: int = 1, limit: int = 28) -> list[dict[str, Any]]:
        """Return list of video dicts for category"""
        category_url = category.get("url")
        # Scrape videos from category URL
        return [
            {
                "title": "Video Title",
                "url": "https://example.com/video/123",
                "thumbnail": "https://example.com/thumb.jpg",
                "duration": "10:30",
                "description": "Video description"
            }
        ]
```

### Provider Method Specifications

#### `get_categories() -> list[dict[str, str]]`

**Purpose**: Return browsable content categories

**Returns**: List of category dictionaries

**Category Dict Schema**:
```python
{
    "name": str,              # Required: Category display name
    "url": str,               # Required: Category URL for get_media_items()
    "icon": str,              # Optional: Icon filename
    "provider_id": str        # Auto-added by ProviderManager
}
```

**Example**:
```python
def get_categories(self):
    return [
        {
            "name": "New Videos",
            "url": "https://example.com/new",
            "icon": "new.png"
        },
        {
            "name": "Popular",
            "url": "https://example.com/popular",
            "icon": "popular.png"
        }
    ]
```

**Notes**:
- Categories sorted alphabetically by `ProviderManager` before returning
- `provider_id` automatically added by `ProviderManager`
- Called when user browses provider content

#### `get_media_items(category: dict, page: int = 1, limit: int = 28) -> list[dict[str, Any]]`

**Purpose**: Return videos/streams for a category

**Parameters**:
- `category`: Category dict from `get_categories()`
- `page`: Page number (1-indexed)
- `limit`: Max items per page

**Returns**: List of media item dictionaries

**Media Item Dict Schema**:
```python
{
    "title": str,             # Required: Video title
    "url": str,               # Required: Video page URL (for resolver)
    "thumbnail": str,         # Optional: Thumbnail URL
    "duration": str,          # Optional: Duration string (e.g., "10:30")
    "description": str,       # Optional: Video description
    "quality": str,           # Optional: Quality indicator
    "views": str,             # Optional: View count
    "date": str,              # Optional: Upload date
    "channel": str,           # Optional: Channel/uploader name
    # ... any provider-specific fields
}
```

**Example**:
```python
def get_media_items(self, category, page=1, limit=28):
    category_url = category.get("url")
    html = self.session.get(category_url).text
    
    videos = []
    # Parse HTML to extract videos
    for video_element in parse_videos(html):
        videos.append({
            "title": extract_title(video_element),
            "url": extract_url(video_element),
            "thumbnail": extract_thumbnail(video_element),
            "duration": extract_duration(video_element)
        })
    
    return videos[:limit]
```

**Notes**:
- Extract `category["url"]` for scraping
- Pagination handled by provider-specific logic
- Return empty list if category has no items

#### `search_videos(term: str, page: int = 1, limit: int = 28) -> list[dict[str, Any]]`

**Purpose**: Search provider content

**Parameters**:
- `term`: Search query string
- `page`: Page number
- `limit`: Max results per page

**Returns**: List of media item dicts (same schema as `get_media_items()`)

**Example**:
```python
def search_videos(self, term, page=1, limit=28):
    search_url = f"{self.base_url}/search?q={urllib.parse.quote(term)}&page={page}"
    html = self.session.get(search_url).text
    
    # Parse search results (same format as get_media_items)
    return parse_video_list(html)[:limit]
```

**Notes**:
- Only required if `supports_search = True`
- Return empty list if no results
- URL encoding handled by provider

---

## Resolver Implementation: `resolver.py`

### Base Class: `BaseResolver`

**Location**: `base_resolver.py`

**Purpose**: Common URL resolution functionality

**Key Attributes**:
```python
self.session: requests.Session  # HTTP session for requests
```

**Key Methods**:
```python
def _is_template_url(self, url: str) -> bool
def _resolve_template_url(self, url: str, quality: str = "best") -> str
def determine_recorder_type(self, url: str) -> str
```

### Required Resolver Class

**Pattern**: Every resolver must implement `class Resolver(BaseResolver)`

**Minimal Implementation**:
```python
from base_resolver import BaseResolver
from debug import get_logger

logger = get_logger(__file__)

class Resolver(BaseResolver):
    def __init__(self):
        super().__init__()
        self.name = "example"
    
    def resolve_url(self, args: dict) -> dict[str, Any] | None:
        """Resolve video page URL to streamable URL"""
        url = args.get("url", "")
        quality = args.get("quality", "best")
        
        logger.info("Resolving URL: %s", url)
        
        # Extract streaming URL from page
        streaming_url = extract_streaming_url(url)
        
        # Determine recorder type
        recorder_id = self.determine_recorder_type(streaming_url)
        
        return {
            "resolved_url": streaming_url,
            "auth_tokens": None,
            "session": self.session,
            "resolved": True,
            "resolver": self.name,
            "recorder_id": recorder_id,
        }
```

### Resolver Method Specification

#### `resolve_url(args: dict) -> dict[str, Any] | None`

**Purpose**: Convert video page URL to streamable URL for recording

**Parameters** (via `args` dict):
- `url`: Video page URL
- `quality`: Quality preference ("best", "720p", "480p", etc.)
- `av1`: AV1 codec preference (True/False/None)

**Returns**: Resolver result dictionary or None on failure

**Resolver Result Schema**:
```python
{
    "resolved_url": str,          # Required: Streamable URL
    "auth_tokens": dict | None,   # Optional: Auth tokens from AuthTokens.to_dict()
    "session": Session | None,    # Optional: Authenticated session
    "ffmpeg_headers": str | None, # Optional: FFmpeg headers for M4S recorder
    "resolved": bool,             # Required: True if resolution succeeded
    "resolver": str,              # Required: Resolver name
    "recorder_id": str,           # Required: Recorder type ("mp4", "hls_basic", etc.)
    "quality": str,               # Optional: Selected quality
}
```

**Example (Simple Passthrough)**:
```python
def resolve_url(self, args):
    url = args.get("url", "")
    
    # URL is already streamable (e.g., PlutoTV HLS)
    recorder_id = self.determine_recorder_type(url)
    
    return {
        "resolved_url": url,
        "auth_tokens": None,
        "session": self.session,
        "resolved": True,
        "resolver": self.name,
        "recorder_id": recorder_id,
    }
```

**Example (With Authentication)**:
```python
def resolve_url(self, args):
    url = args.get("url", "")
    quality = args.get("quality", "best")
    
    # Authenticate and fetch page
    self.auth_tokens = AuthTokens()
    html = self.auth_tokens.fetch_with_fallback(url, self.base_url)
    
    # Parse streaming URLs
    sources = parse_sources_from_html(html)
    
    # Select best quality
    best_source = select_best_source(sources, quality)
    streaming_url = best_source["url"]
    
    # Configure session with provider-specific headers
    session = self.auth_tokens.session
    if session:
        session.headers.update({
            "Referer": self.base_url,
            "Origin": self.base_url
        })
    
    recorder_id = self.determine_recorder_type(streaming_url)
    
    return {
        "resolved_url": streaming_url,
        "auth_tokens": self.auth_tokens.to_dict(),
        "session": session,
        "ffmpeg_headers": self.auth_tokens.get_ffmpeg_headers(),
        "resolved": True,
        "resolver": self.name,
        "recorder_id": recorder_id,
        "quality": quality,
    }
```

**Notes**:
- Return `None` on failure (all resolution methods failed)
- Set provider-specific headers in session before returning
- Use `AuthTokens` for anti-bot protection (see `auth.md`)
- Use `select_best_source()` for quality selection (see `quality_utils.py`)

#### `determine_recorder_type(url: str) -> str`

**Purpose**: Select appropriate recorder based on URL characteristics

**Parameters**:
- `url`: Resolved streaming URL

**Returns**: Recorder ID string

**Recorder Types**:
- `"mp4"`: Direct MP4 downloads
- `"hls_basic"`: Basic HLS playlists (TS segments)
- `"hls_m4s"`: HLS with MP4/M4S segments
- `"hls_live"`: Live HLS streams

**Default Implementation (BaseResolver)**:
```python
def determine_recorder_type(self, url: str) -> str:
    url_lower = url.lower()
    
    # Check for HLS formats
    if '.m3u8' in url_lower:
        # MP4/M4S segment-based HLS
        if ('.m4s.m3u8' in url_lower or 'm4s' in url_lower
                or '.av1.mp4.m3u8' in url_lower or '.mp4.m3u8' in url_lower):
            return 'hls_m4s'
        # Live streaming
        if 'live' in url_lower or 'stream' in url_lower:
            return 'hls_live'
        # Default HLS
        return 'hls_basic'
    
    # Default to MP4 for direct video files
    return 'mp4'
```

**Override Example (xHamster)**:
```python
def determine_recorder_type(self, url: str) -> str:
    url_lower = url.lower()
    
    if '.m3u8' in url_lower:
        # Use base logic for HLS
        return super().determine_recorder_type(url)
    
    # xHamster typically uses MP4 for direct files
    return 'mp4'
```

**Notes**:
- Override only if provider needs custom logic
- Base implementation handles most cases
- Recorder determines download strategy

---

## Provider Manager

### Purpose
Manages provider loading, configuration, and lifecycle.

### Location
`provider_manager.py`

### Key Methods

#### `get_providers() -> list[dict[str, str]]`

**Purpose**: List all available providers with metadata

**Returns**: List of provider config dicts (from `config.json`)

**Process**:
1. Scan `providers/` directory
2. Load `config.json` from each provider directory
3. Add `provider_id` from directory name
4. Sort alphabetically by title/name
5. Return list

**Example Return**:
```python
[
    {
        "provider_id": "PlutoTV",
        "title": "Pluto TV",
        "thumbnail": "plutotv_logo.png",
        "description": "Free live TV"
    },
    {
        "provider_id": "xHamster",
        "title": "xHamster",
        "thumbnail": "xhamster_logo.png",
        "description": "Adult video streaming"
    }
]
```

#### `load_provider(provider_id: str, data_dir: str) -> Any | None`

**Purpose**: Load provider module and create instance

**Parameters**:
- `provider_id`: Provider directory name
- `data_dir`: Path for provider data storage

**Returns**: Provider instance or None on failure

**Process**:
1. Import `providers.{provider_id}` module
2. Get `Provider` class from module
3. Create instance: `Provider(provider_id, data_dir)`
4. Store in `self.providers` dict
5. Return instance

**Example Usage**:
```python
manager = ProviderManager()
provider = manager.load_provider("xHamster", "/data/xHamster")
categories = provider.get_categories()
```

#### `get_provider(provider_id: str, data_dir: str) -> Any | None`

**Purpose**: Get provider instance (load if not cached)

**Process**:
1. If different from `active_provider`, unload previous
2. If not in `self.providers`, call `load_provider()`
3. Set as `active_provider`
4. Return instance

**Notes**:
- Caches provider instances
- Only one active provider at a time
- Auto-loads on first access

#### `unload_provider(provider_id: str) -> bool`

**Purpose**: Remove provider from memory

**Process**:
1. Call `provider.stop_updates()` if available
2. Delete from `self.providers` dict
3. Clear `active_provider` if it was active
4. Return success boolean

---

## Resolver Manager

### Purpose
Manages resolver loading and lifecycle (separate from providers).

### Location
`resolver_manager.py`

### Key Methods

#### `load_resolver(provider_id: str) -> Any | None`

**Purpose**: Load resolver module and create instance

**Process**:
1. Import `providers.{provider_id}.resolver` module
2. Get `Resolver` class from module
3. Create instance: `Resolver()`
4. Store in `self.resolvers` dict
5. Return instance

#### `get_resolver(provider_id: str) -> Any | None`

**Purpose**: Get resolver instance (load if not cached)

**Notes**:
- Similar to ProviderManager.get_provider()
- Only one active resolver at a time
- Used by socket server for URL resolution

---

## Provider Lifecycle

### 1. Discovery
```python
# Socket server starts
manager = ProviderManager()
providers = manager.get_providers()
# → List of provider configs for UI
```

### 2. Provider Activation
```python
# User selects provider in UI
provider = manager.get_provider("xHamster", "/data/xHamster")
# → Provider instance loaded and cached
```

### 3. Category Browsing
```python
# User browses content
categories = provider.get_categories()
# → List of category dicts
```

### 4. Content Discovery
```python
# User selects category
videos = provider.get_media_items(category, page=1, limit=28)
# → List of video dicts
```

### 5. URL Resolution (for recording)
```python
# User initiates recording
resolver = resolver_manager.get_resolver("xHamster")
result = resolver.resolve_url({"url": video_url, "quality": "best"})
# → Resolver result with streaming URL and session
```

### 6. Recording
```python
# Recorder receives resolver result
recorder = get_recorder(result["recorder_id"])
recorder.record_start(result)
recorder.record_stream(result["resolved_url"], output_dir)
# → Video downloaded
```

### 7. Provider Deactivation
```python
# User switches providers or server stops
manager.unload_provider("xHamster")
# → Provider cleanup and memory release
```

---

## Common Patterns

### Pattern 1: Simple Passthrough Provider (PlutoTV)

**Characteristics**:
- URLs already streamable (HLS)
- No authentication required
- No HTML parsing for resolution

**Provider**:
```python
def get_categories(self):
    # Fetch API data
    data = self.fetch_json()
    return parse_categories(data)

def get_media_items(self, category, page=1, limit=28):
    # Return channels from category
    return category["channels"]
```

**Resolver**:
```python
def resolve_url(self, args):
    url = args.get("url", "")
    # URL is already HLS - passthrough
    return {
        "resolved_url": url,
        "session": self.session,
        "resolved": True,
        "resolver": self.name,
        "recorder_id": self.determine_recorder_type(url),
    }
```

### Pattern 2: Authenticated Provider (xHamster)

**Characteristics**:
- Requires anti-bot bypass (CloudFlare)
- HTML parsing for video URLs
- Quality selection from multiple sources
- Provider-specific headers

**Provider**:
```python
def __init__(self, provider_id, data_dir):
    super().__init__(provider_id, data_dir)
    # Set provider-specific headers
    self.session.headers.update({
        "Referer": "https://xhamster.com/",
        "Origin": "https://xhamster.com"
    })
    # Modular components
    self.category_manager = Category(self)
    self.video_manager = Video(self)

def get_categories(self):
    return self.category_manager.get_categories()

def get_media_items(self, category, page=1, limit=28):
    return self.video_manager.get_media_items(category, page, limit)
```

**Resolver**:
```python
def resolve_url(self, args):
    url = args.get("url", "")
    quality = args.get("quality", "best")
    
    # Authenticate with fallback methods
    self.auth_tokens = AuthTokens()
    html = self.auth_tokens.fetch_with_fallback(url, "https://xhamster.com")
    
    # Parse available sources
    sources = self._parse_html_for_sources(html)
    
    # Select best quality
    best_source = select_best_source(sources, quality, codec_aware=True)
    streaming_url = best_source["url"]
    
    # Configure session with critical headers
    session = self.auth_tokens.session
    if session:
        session.headers["Referer"] = "https://xhamster.com/"
        session.headers["Origin"] = "https://xhamster.com"
    
    return {
        "resolved_url": streaming_url,
        "auth_tokens": self.auth_tokens.to_dict(),
        "session": session,
        "ffmpeg_headers": self.auth_tokens.get_ffmpeg_headers(),
        "resolved": True,
        "resolver": self.name,
        "recorder_id": self.determine_recorder_type(streaming_url),
        "quality": quality,
    }
```

### Pattern 3: Modular Provider (xHamster)

**Characteristics**:
- Complex logic split into separate files
- Category, Video, Provider separation
- Shared session and utilities

**Directory Structure**:
```
xHamster/
├── provider.py      # Main provider class (orchestrator)
├── resolver.py      # URL resolver
├── category.py      # Category manager
├── video.py         # Video manager
└── config.json      # Metadata
```

**Provider** (orchestrator):
```python
class Provider(BaseProvider):
    def __init__(self, provider_id, data_dir):
        super().__init__(provider_id, data_dir)
        self.category_manager = Category(self)
        self.video_manager = Video(self)
    
    def get_categories(self):
        return self.category_manager.get_categories()
    
    def get_media_items(self, category, page=1, limit=28):
        return self.video_manager.get_media_items(category, page, limit)
```

**Category** (category.py):
```python
class Category:
    def __init__(self, provider):
        self.provider = provider
        self.session = provider.session
    
    def get_categories(self):
        # Scrape categories
        return [...]
```

**Video** (video.py):
```python
class Video:
    def __init__(self, provider):
        self.provider = provider
        self.session = provider.session
    
    def get_media_items(self, category, page, limit):
        # Scrape videos
        return [...]
```

---

## Integration with Recorders

### Resolver → Recorder Flow

1. **Resolver resolves URL**:
   ```python
   result = resolver.resolve_url({"url": video_url, "quality": "best"})
   ```

2. **Result passed to recorder**:
   ```python
   recorder = get_recorder(result["recorder_id"])
   recorder.record_start(result)
   ```

3. **Recorder extracts session**:
   ```python
   def record_start(self, resolve_result):
       self.session = resolve_result.get("session")
       self.auth_tokens = resolve_result.get("auth_tokens")
       if not self.session:
           self.session = requests.Session()  # Fallback
   ```

4. **Recorder uses session for download**:
   ```python
   def record_stream(self, url, rec_dir):
       with self.session.get(url, stream=True) as response:
           # Download with authenticated session
   ```

### Critical Points

**Session Transfer**: Resolver creates authenticated session, recorder reuses it
- Preserves authentication cookies
- Maintains provider-specific headers
- Enables connection pooling

**Recorder Selection**: Resolver determines recorder type
- `mp4`: Direct MP4 downloads
- `hls_basic`: TS-based HLS
- `hls_m4s`: MP4/M4S-based HLS
- `hls_live`: Live streams

**Provider-Specific Headers**: Set in resolver, preserved in session
- Referer, Origin for CORS
- Custom headers for CDN access
- Authentication tokens

---

## Error Handling

### Provider Errors

**Missing Categories**:
```python
def get_categories(self):
    try:
        categories = scrape_categories()
        return categories if categories else []
    except Exception as e:
        logger.error("Failed to fetch categories: %s", e)
        return []  # Return empty list, not None
```

**Scraping Failures**:
```python
def get_media_items(self, category, page=1, limit=28):
    try:
        html = self.session.get(category["url"]).text
        videos = parse_videos(html)
        return videos[:limit]
    except Exception as e:
        logger.error("Failed to fetch media items: %s", e)
        return []  # Return empty list
```

### Resolver Errors

**Resolution Failure**:
```python
def resolve_url(self, args):
    url = args.get("url", "")
    
    try:
        streaming_url = extract_url(url)
        if not streaming_url:
            logger.error("No streaming URL found")
            return None  # Failure
        
        return {
            "resolved_url": streaming_url,
            "resolved": True,
            # ...
        }
    except Exception as e:
        logger.error("Resolution failed: %s", e)
        return None  # Failure
```

**Fallback Chain** (with AuthTokens):
```python
html = self.auth_tokens.fetch_with_fallback(url, domain)
if not html:
    logger.error("All auth methods failed")
    return None
# Continue with HTML parsing
```

---

## Testing

### Manual Provider Testing

**Test Categories**:
```python
from provider_manager import ProviderManager

manager = ProviderManager()
provider = manager.get_provider("xHamster", "/tmp/test_data")

categories = provider.get_categories()
assert len(categories) > 0
assert all("name" in c and "url" in c for c in categories)
```

**Test Media Items**:
```python
category = categories[0]
videos = provider.get_media_items(category, page=1, limit=10)

assert len(videos) > 0
assert all("title" in v and "url" in v for v in videos)
```

### Manual Resolver Testing

**Test Resolution**:
```python
from resolver_manager import ResolverManager

manager = ResolverManager()
resolver = manager.get_resolver("xHamster")

result = resolver.resolve_url({
    "url": "https://xhamster.com/videos/example-123",
    "quality": "best"
})

assert result is not None
assert result["resolved"] is True
assert result["resolved_url"].startswith("http")
assert result["recorder_id"] in ["mp4", "hls_basic", "hls_m4s", "hls_live"]
```

### Integration Testing

**Full Flow**:
```python
# 1. Get provider
provider = provider_manager.get_provider("xHamster", "/tmp/data")

# 2. Get content
categories = provider.get_categories()
videos = provider.get_media_items(categories[0])
video_url = videos[0]["url"]

# 3. Resolve URL
resolver = resolver_manager.get_resolver("xHamster")
result = resolver.resolve_url({"url": video_url, "quality": "best"})

# 4. Test recorder (mock)
assert result["session"] is not None
assert result["resolved_url"] != video_url  # URL was resolved
```

---

## Creating a New Provider

### Step-by-Step Guide

#### 1. Copy Template
```bash
cp -r src/streamingserver/providers/TEMPLATE_PROVIDER src/streamingserver/providers/NewProvider
```

#### 2. Update `config.json`
```json
{
  "title": "New Provider",
  "thumbnail": "newprovider_logo.png",
  "description": "Description of New Provider"
}
```

#### 3. Implement Provider (`provider.py`)

**Minimal**:
```python
from base_provider import BaseProvider

class Provider(BaseProvider):
    def __init__(self, provider_id, data_dir):
        super().__init__(provider_id, data_dir)
        self.name = "NewProvider"
        self.base_url = "https://newprovider.com"
        self.supports_categories = True
    
    def get_categories(self):
        # Scrape or fetch categories
        return [
            {"name": "Category 1", "url": "https://newprovider.com/cat1"},
            {"name": "Category 2", "url": "https://newprovider.com/cat2"}
        ]
    
    def get_media_items(self, category, page=1, limit=28):
        # Scrape videos from category URL
        category_url = category.get("url")
        # ... scraping logic ...
        return [
            {
                "title": "Video Title",
                "url": "https://newprovider.com/video/123",
                "thumbnail": "https://newprovider.com/thumb.jpg"
            }
        ]
```

#### 4. Implement Resolver (`resolver.py`)

**Simple Passthrough**:
```python
from base_resolver import BaseResolver
from debug import get_logger

logger = get_logger(__file__)

class Resolver(BaseResolver):
    def __init__(self):
        super().__init__()
        self.name = "newprovider"
    
    def resolve_url(self, args):
        url = args.get("url", "")
        
        # URL is already streamable
        recorder_id = self.determine_recorder_type(url)
        
        return {
            "resolved_url": url,
            "session": self.session,
            "resolved": True,
            "resolver": self.name,
            "recorder_id": recorder_id,
        }
```

**With HTML Parsing**:
```python
def resolve_url(self, args):
    url = args.get("url", "")
    quality = args.get("quality", "best")
    
    # Fetch page
    response = self.session.get(url)
    html = response.text
    
    # Parse streaming URL
    streaming_url = extract_streaming_url(html)
    
    # Select quality if multiple sources
    if isinstance(streaming_url, list):
        streaming_url = select_best_source(streaming_url, quality)["url"]
    
    recorder_id = self.determine_recorder_type(streaming_url)
    
    return {
        "resolved_url": streaming_url,
        "session": self.session,
        "resolved": True,
        "resolver": self.name,
        "recorder_id": recorder_id,
        "quality": quality,
    }
```

#### 5. Test Provider

```python
# Test loading
manager = ProviderManager()
providers = manager.get_providers()
assert "NewProvider" in [p["provider_id"] for p in providers]

# Test functionality
provider = manager.get_provider("NewProvider", "/tmp/test")
categories = provider.get_categories()
assert len(categories) > 0

videos = provider.get_media_items(categories[0])
assert len(videos) > 0

# Test resolver
resolver_manager = ResolverManager()
resolver = resolver_manager.get_resolver("NewProvider")
result = resolver.resolve_url({"url": videos[0]["url"], "quality": "best"})
assert result["resolved"] is True
```

#### 6. Add to Build System

**Update `src/streamingserver/providers/Makefile.am`**:
```makefile
SUBDIRS = PlutoTV SamsungTV xHamster NewProvider
```

---

## Best Practices

### Do's

1. **Inherit from base classes**
   - Provider → BaseProvider
   - Resolver → BaseResolver

2. **Use session from base**
   - `self.session` for HTTP requests
   - Preserves cookies and headers

3. **Return proper data structures**
   - Categories: List of dicts with name/url
   - Videos: List of dicts with title/url
   - Resolver: Dict with resolved_url/session/recorder_id

4. **Handle errors gracefully**
   - Return empty list on failure (not None)
   - Return None from resolver on failure
   - Log errors with logger

5. **Set provider-specific headers in resolver**
   - Referer, Origin for CORS
   - Custom headers for CDN access
   - Before returning result

6. **Use utility functions**
   - `select_best_source()` for quality selection
   - `AuthTokens` for anti-bot protection
   - `get_session()` for session creation

### Don'ts

1. **Don't put provider-specific logic in recorders**
   - Headers/cookies in resolver, not recorder
   - Recorder should be generic

2. **Don't return None for empty results**
   - Provider methods: Return empty list `[]`
   - Resolver: Return None only on error

3. **Don't hardcode recorder types**
   - Use `determine_recorder_type()` in resolver
   - Override only if provider needs custom logic

4. **Don't create sessions directly**
   - Use `self.session` from base class
   - Use `get_session()` if needed

5. **Don't forget session cleanup**
   - Implement `stop_updates()` if using threads
   - Close sessions in cleanup

---

## Design Decisions

### Why separate Provider and Resolver?

**Reason**: Different responsibilities and timing
- Provider: Browsing (no auth needed, fast)
- Resolver: Recording (auth needed, slower)
- User can browse without triggering authentication

### Why use class name `Provider` and `Resolver`?

**Reason**: Consistent discovery pattern
- ProviderManager looks for `Provider` class
- ResolverManager looks for `Resolver` class
- No need to configure class names

### Why pass `provider_id` and `data_dir` to provider?

**Reason**: Provider isolation
- Each provider stores data separately
- Provider ID for logging/debugging
- Data dir for caching, categories, thumbnails

### Why not pass data_dir to resolver?

**Reason**: Resolvers are stateless
- No persistent data needed
- Session created per-request
- Simplified lifecycle

### Why single active provider/resolver?

**Reason**: Memory efficiency
- Only one provider used at a time
- Unload previous when switching
- Prevents memory leaks

### Why not standardize video metadata schema?

**Reason**: Provider flexibility
- Different providers have different data
- UI can adapt to available fields
- Only title/url strictly required

---

## Future Enhancements

### Potential Improvements

1. **Provider Capabilities Discovery**
   - Report supported features (categories, search, quality selection)
   - Dynamic UI based on capabilities

2. **Async Provider Methods**
   - Non-blocking category/video fetching
   - Better performance for slow providers

3. **Provider Configuration UI**
   - Per-provider settings (quality defaults, language)
   - User preferences per provider

4. **Caching Layer**
   - Cache categories/videos for performance
   - Configurable TTL per provider

5. **Provider Versioning**
   - Version in config.json
   - Migration support for config changes

6. **Automated Testing**
   - Unit tests for each provider
   - Integration tests for full flow
   - CI/CD validation

---

## Summary

The provider system enables modular integration of streaming services through:

1. **Provider**: Content discovery (categories, videos, search)
2. **Resolver**: URL resolution for recording (auth, quality selection)
3. **Manager**: Provider/resolver loading and lifecycle
4. **Base Classes**: Common functionality and utilities

**Key Principles**:
- Separation of concerns (browsing vs recording)
- Consistent interfaces for all providers
- Provider-specific logic isolated in provider directory
- Session management for authentication and performance
- Flexible data schemas for provider-specific metadata

**To Add a Provider**:
1. Copy TEMPLATE_PROVIDER directory
2. Update config.json
3. Implement get_categories() and get_media_items()
4. Implement resolve_url() in resolver
5. Test with ProviderManager and ResolverManager
