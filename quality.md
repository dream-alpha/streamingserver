# Quality Selection System Specification

## Overview

The quality selection system provides intelligent video quality selection for the streaming server. It handles various video formats (MP4, HLS), supports codec-aware selection, and can analyze HLS adaptive streaming playlists to determine available quality levels.

## Architecture

### Components

1. **quality_utils.py** - Core quality selection logic
2. **hls_quality_analyzer.py** - HLS playlist analysis for adaptive streams
3. **Provider config.json** - Per-provider quality defaults

### Quality Configuration Flow

```
Provider config.json → ProviderManager → RequestHandler → Resolver → select_best_source()
                                                           ↓
                                                    Quality Selection
```

## Quality Levels

### Standard Quality Labels

The system uses standardized quality labels ordered from highest to lowest:

```python
["adaptive", "2160p", "1440p", "1080p", "720p", "480p", "360p", "240p", "144p"]
```

- **adaptive** - HLS adaptive streaming (treated as highest quality initially)
- **2160p** - 4K Ultra HD (3840×2160)
- **1440p** - 2K Quad HD (2560×1440)
- **1080p** - Full HD (1920×1080)
- **720p** - HD (1280×720)
- **480p** - SD (854×480)
- **360p** - Low (640×360)
- **240p** - Very Low (426×240)
- **144p** - Mobile (256×144)

### Quality Input Formats

The system accepts quality strings in standard format only:

- **"best"** - Select highest available quality
- **"adaptive"** - HLS adaptive streaming
- **"2160p", "1440p", "1080p", "720p", "480p", "360p", "240p", "144p"** - Standard progressive resolutions with 'p' suffix

## Provider Quality Configuration

### Configuration Location

Each provider has a `config.json` file with a quality field:

```json
{
  "title": "Provider Name",
  "thumbnail": "icon.png",
  "description": "Provider description",
  "quality": "720p"
}
```

### Provider Quality Defaults

Different providers have different optimal quality settings:

- **Live TV Providers** (PlutoTV, SamsungTV): `"best"` - Maximum quality for live content
- **Adult Content Providers** (xHamster, XVideos, XNXX): `"720p"` - Balance of quality and compatibility
- **Template/Default**: `"best"` - Safe default for new providers

### Quality Selection Hierarchy

The system uses provider-specific quality **only** - client quality override has been removed:

```
Provider config quality (always used)
```

This ensures consistent quality behavior per provider based on tested optimal settings.

## Quality Selection Process

### Main Function: `select_best_source()`

```python
select_best_source(
    sources,                    # List of source dictionaries
    preferred_quality="best",   # Target quality
    codec_aware=True,          # Prefer better codecs
    av1=None,                  # AV1 codec support
    debug_output=True,         # Detailed logging
    analyze_hls=True           # HLS quality analysis
)
```

### Selection Algorithm

#### Step 1: HLS Quality Enhancement (Optional)

If `analyze_hls=True`:

1. Identify HLS sources (`quality="adaptive"`, `format="m3u8"`)
2. Fetch and parse HLS master playlist
3. Extract available quality levels from stream variants
4. Update source quality from "adaptive" to actual max quality (e.g., "1080p")
5. Store HLS analysis data in source for matching

#### Step 2: Quality Matching

**Case 1: Only One Source**
- Return the only available source

**Case 2: "best" Quality Requested**
- Sort sources by quality priority
- Select highest quality source
- If codec_aware=True and multiple sources with same quality:
  - Apply codec preference: AV1 > H.265 > H.264
- If av1=False and quality is 2160p:
  - Filter out 2160p sources (4K typically requires AV1)

**Case 3: Specific Quality Requested**

1. **Find Exact Match**:
   - Check for direct quality match
   - Check HLS streams that contain the target quality
   - If multiple exact matches and codec_aware=True:
     - Prefer better codec

2. **Find Closest Match** (if no exact match):
   - For numeric qualities (e.g., "720p"):
     - Calculate numeric distance from target
     - HLS streams with exact quality get distance=0
     - Select source with smallest distance
   - For non-numeric qualities:
     - Use quality priority order
     - Calculate index distance
     - Select closest match

### Source Data Structure

Each source dictionary contains:

```python
{
    "url": str,              # Video URL
    "quality": str,          # Quality label (e.g., "720p")
    "format": str,           # Format (e.g., "mp4", "m3u8")
    "codec": str,            # Optional: codec (e.g., "h264", "h265", "av1")
    
    # HLS Enhancement (if analyzed)
    "hls_analysis": {
        "qualities": ["1080p", "720p", "480p"],  # Available qualities
        "streams": [...],                         # Stream details
        "max_quality": "1080p",                   # Highest quality
        "has_adaptive": True                      # Is adaptive stream
    },
    "original_quality": "adaptive"  # Original quality before enhancement
}
```

## Codec Selection

### Codec Priority

When `codec_aware=True` and multiple sources have the same quality:

```
Priority: AV1 > H.265/HEVC > H.264/AVC > Unknown
```

### Codec Detection

1. **Explicit codec field**: Use `source["codec"]` if present
2. **URL pattern matching**:
   - AV1: `.av1.`, `_av1_`, `av1-`
   - H.265: `.h265.`, `_h265_`, `.hevc.`, `_hevc_`
   - H.264: `.h264.`, `_h264_`, `.avc.`, `_avc_`

### AV1 Filtering

When `av1=False`:
- Filter out sources with AV1 codec
- Filter out 2160p sources (4K typically requires AV1)
- Fallback to original sources if all filtered out

## HLS Quality Analysis

### Purpose

Convert generic "adaptive" quality label to specific quality levels by analyzing HLS master playlists.

### Process

1. **Fetch Master Playlist**:
   ```python
   analyze_hls_qualities(url, session=None)
   ```

2. **Parse Variants**:
   - Extract resolution (width×height) from each stream
   - Extract bandwidth information
   - Convert to quality labels

3. **Quality Inference**:
   - **From Resolution**: `1920×1080 → "1080p"`
   - **From Bandwidth**: 
     - ≥8 Mbps → "1080p"
     - ≥4 Mbps → "720p"
     - ≥2 Mbps → "480p"
     - ≥1 Mbps → "360p"
     - ≥500 kbps → "240p"
     - <500 kbps → "144p"

4. **Enhancement Result**:
   ```python
   {
       'qualities': ['1080p', '720p', '480p'],
       'streams': [...],
       'max_quality': '1080p',
       'has_adaptive': True,
       'error': None
   }
   ```

### HLS-Enhanced Matching

When matching quality against HLS sources:

1. Check direct quality match
2. **Check if HLS stream contains target quality**:
   - If "720p" requested and HLS has ['1080p', '720p', '480p']
   - Consider this an exact match
3. For closest match, HLS streams with target quality get distance=0

### Fallback Behavior

If HLS analysis fails:
- Keep original source with `quality="adaptive"`
- Continue with quality selection
- Log warning about failed analysis

## Integration Points

### 1. Provider Config Loading

**File**: `provider_manager.py`

```python
config = {
    'quality': 'best',  # Default quality
    ...
}
# Load from config.json, merge with defaults
```

### 2. Request Handling

**File**: `socket_request_handler.py`

On "start" command:
1. Load provider list from ProviderManager
2. Find provider config by provider_id
3. Set quality in args from provider config
4. Pass args to resolver

```python
provider_config = next((p for p in providers_list 
                       if p.get("provider_id") == provider_id), None)
if provider_config:
    args["quality"] = provider_config.get("quality", "best")
```

### 3. Resolver Usage

**File**: Provider resolvers (e.g., `xHamster/resolver.py`)

#### URL Metadata Extraction

Use `extract_metadata_from_url()` to extract quality/format/codec from URLs:

```python
from quality_utils import select_best_source, extract_metadata_from_url

# Extract metadata from a URL
url = "https://cdn.example.com/video_1080p_h264.mp4"
metadata = extract_metadata_from_url(url)
# Returns: {"quality": "1080p", "format": "mp4", "codec": "h264"}

# Build source with extracted metadata
sources.append({"url": url, **metadata})

# Or override/supplement extracted values
metadata = extract_metadata_from_url(url)
if not metadata["quality"]:
    metadata["quality"] = "720p"  # Fallback quality
sources.append({"url": url, **metadata})
```

#### Quality Selection

```python
from quality_utils import select_best_source

# Get quality from args (set by provider config)
quality = args.get("quality", "best")

# Select best source
selected_source = select_best_source(
    sources,
    preferred_quality=quality,
    codec_aware=True,
    av1=None,  # Auto-detect
    debug_output=True,
    analyze_hls=True
)
```

## Helper Functions

### extract_metadata_from_url()

Centralized function to extract video metadata from URL patterns.

**Purpose**: Eliminate duplicated pattern matching logic across resolvers.

**Signature**:
```python
def extract_metadata_from_url(url: str) -> dict:
    """
    Extract quality, format, and codec from URL.
    
    Args:
        url: Video URL to analyze
        
    Returns:
        dict with keys: quality, format, codec (all str or None)
    """
```

**Extraction Logic**:

1. **Format Detection**:
   - `.m3u8` → `"m3u8"`
   - `.mp4` → `"mp4"`
   
2. **Quality Extraction**:
   - Regex: `(\d+p)` in URL path (ignores query parameters)
   - Examples: `video_1080p.mp4` → `"1080p"`, `stream-720p-high.m3u8` → `"720p"`
   
3. **Codec Detection**:
   - AV1 patterns: `.av1.`, `_av1_`, `av1-`, `/av1/`
   - H.265 patterns: `.h265.`, `_h265_`, `.hevc.`, `_hevc_`, `h265-`, `hevc-`
   - H.264 patterns: `.h264.`, `_h264_`, `.avc.`, `_avc_`, `h264-`, `avc-`

**Example Usage**:
```python
# Example 1: Full metadata extraction
url = "https://cdn.example.com/video_1080p_h265.mp4"
metadata = extract_metadata_from_url(url)
# Result: {"quality": "1080p", "format": "mp4", "codec": "h265"}

# Example 2: HLS stream
url = "https://stream.example.com/master.m3u8"
metadata = extract_metadata_from_url(url)
# Result: {"quality": None, "format": "m3u8", "codec": None}

# Example 3: Build source dict
sources.append({"url": url, **metadata})
# Creates: {"url": "...", "quality": "1080p", "format": "mp4", "codec": "h265"}
```

**Fallback Strategy**:
```python
metadata = extract_metadata_from_url(url)

# Provide fallbacks for missing data
if not metadata["quality"]:
    metadata["quality"] = "adaptive" if metadata["format"] == "m3u8" else "480p"
if not metadata["codec"]:
    metadata["codec"] = "h264"  # Assume H.264 as default

sources.append({"url": url, **metadata})
```

## Debug Output

### Selection Process Logging

When `debug_output=True`, the system logs:

```
=== QUALITY SELECTION DEBUG ===
Available sources (3 total):
  1. 1080p (mp4) [h264] - https://...
  2. 720p (mp4) [h265] - https://...
  3. 1080p (m3u8) (HLS: 1080p, 720p, 480p) - https://...
Selection criteria: quality='720p', codec_aware=True, av1=None
Found exact quality match in HLS stream: 1080p (contains 720p)
=== SELECTION RESULT: 1080p ===
```

### HLS Analysis Logging

```
Analyzing 2 HLS sources for quality information...
Analyzing HLS playlist: https://...
Found 5 stream variants
HLS analysis complete: 3 qualities found: 1080p, 720p, 480p
Enhanced adaptive source: adaptive -> 1080p
HLS quality enhancement completed
```

## Error Handling

### Invalid Input

```python
# Empty sources
if not sources:
    logger.warning("No sources provided for selection")
    return None

# Invalid source format
if not all(isinstance(source, dict) for source in sources):
    raise ValueError("All sources must be dictionaries...")
```

### HLS Analysis Failure

```python
try:
    enhanced_sources = enhance_sources_with_hls_quality(sources)
except Exception as e:
    logger.warning("HLS quality analysis failed, continuing with original sources: %s", e)
    # Continue with original sources
```

### Codec Filtering

```python
# If all sources filtered out due to av1=False
if not filtered_sources:
    logger.warning("All sources filtered out due to av1=False, keeping original sources")
    filtered_sources = sources
```

## Best Practices

### For Provider Developers

1. **Test Quality Settings**: Determine optimal quality for your provider
2. **Set Config Quality**: Add quality field to `config.json` with standard format ("720p", "1080p", "best")
3. **Use select_best_source()**: Don't implement custom quality logic
4. **Provide Rich Sources**: Include quality, format, codec when available - quality MUST include 'p' suffix
5. **Let HLS Analyze**: Set `analyze_hls=True` for HLS sources

### For Resolver Implementation

```python
# Good: Let quality_utils handle selection
sources = [...]  # Extract all available sources
selected = select_best_source(sources, preferred_quality=args.get("quality", "best"))

# Bad: Custom quality selection
for source in sources:
    if source["quality"] == quality:
        return source
```

### Quality Configuration Guidelines

- **Live TV**: Use `"best"` (maximize live stream quality)
- **VOD with compatibility concerns**: Use `"720p"` (balance quality/compatibility)
- **High-end VOD**: Use `"1080p"` or `"best"`
- **Mobile-optimized**: Use `"480p"` or `"360p"`

## Implementation Notes

### Quality Format

The system requires quality values in standard format with 'p' suffix:
- Valid: "720p", "1080p", "best", "adaptive"
- Invalid: "720", "1080", "HD", "SD"

### HLS Analysis Performance

- Only analyzes sources marked as `quality="adaptive"` and `format="m3u8"`
- Fetches playlist with 10-second timeout
- Runs before quality selection
- Failures don't block quality selection

### Codec-Aware Selection

- Only applied when multiple sources have same quality
- AV1 filtering applied at selection time
- Codec detection uses URL patterns as fallback
- Unknown codecs get lowest priority

## Future Enhancements

Potential improvements:

1. **Cache HLS Analysis**: Cache playlist analysis results by URL
2. **Bandwidth Estimation**: Use network speed to filter qualities
3. **DRM-Aware Selection**: Integrate with DRM detection
4. **Quality Validation**: Validate quality values in config.json
5. **Bandwidth-Based Fallback**: Auto-downgrade on slow connections
6. **Per-Recording Quality**: Override provider default per recording

## Summary

The quality selection system provides:

- ✅ Simple, consistent quality selection API
- ✅ Provider-specific quality defaults
- ✅ HLS adaptive streaming analysis
- ✅ Codec-aware selection with AV1 support
- ✅ Intelligent closest-match fallback
- ✅ Comprehensive debug output
- ✅ Robust error handling

Key principle: **Simplicity over complexity** - closest match logic instead of complex scoring algorithms.
