# Copyright (C) 2018-2025 by dream-alpha
# License: GNU General Public License v3.0 (see LICENSE file for details)

from quality_utils import (
    get_quality_score, get_codec_score, extract_codec_from_url, AV1_ENABLED,
    QUALITY_SELECTORS, QUALITY_ALIASES, filter_sources_by_codec
)
from debug import get_logger

logger = get_logger(__file__)


def select_best_source(sources, preferred_quality="best", codec_aware=True, av1=None):
    """
    Select the best source from a list of sources with quality and codec information.

    Args:
        sources (list): List of source dictionaries with 'quality', 'url', 'codec' keys
        preferred_quality (str): Preferred quality ("best" for highest, or specific like "720p")
        codec_aware (bool): Whether to consider codec preferences in selection
        av1 (bool): Whether to include AV1 codecs (None=use global setting, True=force enable, False=force disable)

    Returns:
        dict or None: Selected source dictionary or None if no sources available
    """
    if not sources:
        logger.warning("No sources provided for selection")
        return None

    # Log all available sources for debugging
    logger.debug("=== Available Sources for Selection ===")
    for i, source in enumerate(sources, 1):
        codec = source.get("codec", extract_codec_from_url(source.get("url", "")))
        quality = source.get("quality", "unknown")
        format_type = source.get("format", "unknown")
        url_preview = source.get("url", "")[:60] + "..." if len(source.get("url", "")) > 60 else source.get("url", "")
        logger.debug("Source %d: %s (%s/%s) - %s", i, quality, codec, format_type, url_preview)
    logger.debug("Requested quality: %s, codec_aware: %s, av1: %s", preferred_quality, codec_aware, av1)

    # If only one source, return it
    if len(sources) == 1:
        selected = sources[0]
        logger.info("Only one source available, selecting: %s (%s)",
                    selected.get("quality", "unknown"),
                    selected.get("codec", extract_codec_from_url(selected.get("url", ""))))
        return selected

    # Determine AV1 preference: parameter overrides global setting
    av1_enabled = av1 if av1 is not None else AV1_ENABLED

    # Filter out AV1 sources if AV1 is explicitly disabled
    if av1 is False:  # Only when explicitly set to False (not None)
        # Get all non-AV1 codecs from available sources
        non_av1_codecs = []
        for source in sources:
            codec = source.get("codec", extract_codec_from_url(source.get("url", "")))
            if codec.lower() != "av1" and codec.lower() not in non_av1_codecs:
                non_av1_codecs.append(codec.lower())

        if non_av1_codecs:
            filtered_sources = filter_sources_by_codec(sources, non_av1_codecs)
            if filtered_sources:  # Only use filtered sources if any remain
                sources = filtered_sources
                logger.info("Filtered out AV1 sources, %d sources remaining", len(sources))

    # If requesting best quality and codec-aware, use combined scoring
    if preferred_quality == "best" and codec_aware:
        def combined_score(source):
            quality = source.get("quality", "Unknown")
            codec = source.get("codec", extract_codec_from_url(source.get("url", "")))

            quality_score = get_quality_score(quality) * 100  # Weight quality higher
            codec_score = get_codec_score(codec) if av1_enabled or codec != "av1" else 0

            return quality_score + codec_score

        # Debug: show scoring for each source
        logger.debug("=== Combined Quality+Codec Scoring ===")
        for source in sources:
            score = combined_score(source)
            quality = source.get("quality", "Unknown")
            codec = source.get("codec", extract_codec_from_url(source.get("url", "")))
            logger.debug("%s (%s): score = %d", quality, codec, score)

        best_source = max(sources, key=combined_score)
        logger.info("Selected best quality+codec source: %s (%s)",
                    best_source.get("quality", "unknown"),
                    best_source.get("codec", extract_codec_from_url(best_source.get("url", ""))))
        return best_source

    # If requesting best quality (default), sources are already sorted by resolvers
    # so just take the first one
    if preferred_quality == "best":
        best_source = sources[0]
        logger.info("Selected best quality source: %s", best_source.get("quality", "unknown"))
        return best_source

    # Resolve quality selectors and aliases first

    # Resolve selector to target quality (e.g., "fhd" → "1080p", "max" → "2160p")
    resolved_quality = preferred_quality
    if preferred_quality.lower() in QUALITY_SELECTORS:
        resolved_quality = QUALITY_SELECTORS[preferred_quality.lower()]
        logger.debug("Resolved quality selector '%s' → '%s'", preferred_quality, resolved_quality)

    # Also check aliases (e.g., "FHD" → "1080p")
    resolved_quality = QUALITY_ALIASES.get(resolved_quality, resolved_quality)

    # Look for specific quality match using resolved quality
    matching_sources = []
    for source in sources:
        source_quality = source.get("quality", "").lower()
        if source_quality == resolved_quality.lower():
            matching_sources.append(source)

    if matching_sources:
        if len(matching_sources) == 1:
            selected = matching_sources[0]
            logger.info("Found exact quality match: %s", selected.get("quality", "unknown"))
            return selected
        if codec_aware:
            # Multiple sources with same quality - prefer better codec
            def codec_score_func(source):
                codec = source.get("codec", extract_codec_from_url(source.get("url", "")))
                return get_codec_score(codec) if av1_enabled or codec != "av1" else 0

            selected = max(matching_sources, key=codec_score_func)
            logger.info("Found exact quality match with preferred codec: %s (%s)",
                        selected.get("quality", "unknown"),
                        selected.get("codec", extract_codec_from_url(selected.get("url", ""))))
            return selected
        # Return first match
        selected = matching_sources[0]
        logger.info("Found exact quality match: %s", selected.get("quality", "unknown"))
        return selected

    # If no exact match, find closest quality using scoring
    target_score = get_quality_score(resolved_quality)
    best_source = None
    best_diff = float('inf')

    logger.debug("=== Closest Quality Match Scoring ===")
    logger.debug("Target quality: %s (resolved from: %s), target score: %d", resolved_quality, preferred_quality, target_score)

    for source in sources:
        source_quality = source.get("quality", "")
        source_score = get_quality_score(source_quality)
        diff = abs(source_score - target_score)
        codec = source.get("codec", extract_codec_from_url(source.get("url", "")))
        logger.debug("%s (%s): score = %d, diff = %d", source_quality, codec, source_score, diff)

        if diff < best_diff or (diff == best_diff and codec_aware):
            if diff == best_diff and codec_aware and best_source:
                # Same quality difference - prefer better codec
                current_codec = source.get("codec", extract_codec_from_url(source.get("url", "")))
                best_codec = best_source.get("codec", extract_codec_from_url(best_source.get("url", "")))

                current_codec_score = get_codec_score(current_codec) if av1_enabled or current_codec != "av1" else 0
                best_codec_score = get_codec_score(best_codec) if av1_enabled or best_codec != "av1" else 0

                if current_codec_score > best_codec_score:
                    best_diff = diff
                    best_source = source
            else:
                best_diff = diff
                best_source = source

    if best_source:
        logger.info("Selected closest quality match: %s (%s) (target: %s)",
                    best_source.get("quality", "unknown"),
                    best_source.get("codec", extract_codec_from_url(best_source.get("url", ""))),
                    preferred_quality)
        return best_source

    # Final fallback - return first source
    fallback_source = sources[0]
    logger.info("Using fallback source: %s (%s)",
                fallback_source.get("quality", "unknown"),
                fallback_source.get("codec", extract_codec_from_url(fallback_source.get("url", ""))))
    return fallback_source
