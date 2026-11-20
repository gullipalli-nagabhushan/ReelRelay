package com.reelrelay.app

import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.ObjectMapper
import java.net.URI
import java.util.Locale

/** One video inside a playlist, resolved to a downloadable page URL. */
data class PlaylistEntry(
    val url: String,
    val title: String,
    val durationLabel: String = "",
)

/** A flat playlist listing, capped so a huge channel list stays manageable. */
data class PlaylistListing(
    val title: String,
    val uploader: String,
    val entries: List<PlaylistEntry>,
    val total: Int,
    val url: String,
) {
    val truncated: Boolean get() = total > entries.size
}

/** Only the first [MAX_PLAYLIST_ITEMS] videos of a playlist are queued. */
const val MAX_PLAYLIST_ITEMS = 100

/** Height ladder used when a sample video cannot be read. */
internal val PLAYLIST_HEIGHTS = listOf(2160, 1440, 1080, 720, 480, 360)

private val MAPPER = ObjectMapper()

/**
 * True when a link points at a playlist rather than a single video.
 *
 * `youtube.com/playlist?list=…`, any URL carrying a `list` query parameter and
 * `facebook.com/…/playlist` links all qualify. A `watch?v=…&list=…` link is a
 * playlist too, which matches what yt-dlp does by default.
 */
fun isPlaylistUrl(raw: String): Boolean {
    val url = raw.trim()
    if (!url.startsWith("http://") && !url.startsWith("https://")) return false
    return runCatching {
        val uri = URI(url)
        val path = uri.path.orEmpty().lowercase(Locale.US)
        if (path.contains("/playlist")) return@runCatching true
        val query = uri.rawQuery.orEmpty()
        query.split('&').any { pair ->
            val key = pair.substringBefore('=')
            key.equals("list", ignoreCase = true) && pair.substringAfter('=', "").isNotBlank()
        }
    }.getOrDefault(false)
}

/**
 * Reads playlist listing output.
 *
 * Accepts either the line-delimited JSON produced by `--dump-json
 * --flat-playlist` (what the app uses) or a `--dump-single-json` document.
 * Returns `null` when the payload is not a playlist so the caller can fall back
 * to normal single-video extraction.
 */
fun parsePlaylist(
    payload: String,
    requestedUrl: String,
    limit: Int = MAX_PLAYLIST_ITEMS,
): PlaylistListing? {
    val trimmed = payload.trim()
    if (trimmed.isEmpty()) return null
    val host = runCatching { URI(requestedUrl).host.orEmpty() }.getOrDefault("")

    val entries = mutableListOf<PlaylistEntry>()
    var title = ""
    var uploader = ""

    val single = if (trimmed.startsWith("{")) {
        runCatching { MAPPER.readTree(trimmed) }.getOrNull()
    } else {
        null
    }
    if (single != null && single.path("entries").isArray) {
        single.path("entries").forEach { node -> entry(node, host)?.let(entries::add) }
        title = single.path("title").asText("")
        uploader = single.path("uploader").asText("")
            .ifBlank { single.path("channel").asText("") }
    } else {
        val nodes = trimmed.lineSequence()
            .map { it.trim() }
            .filter { it.startsWith("{") }
            .mapNotNull { line -> runCatching { MAPPER.readTree(line) }.getOrNull() }
            .toList()

        // A single-video dump has no playlist context at all.
        val looksLikePlaylist = nodes.any { node ->
            node.path("playlist_id").asText("").isNotBlank() ||
                node.path("playlist_title").asText("").isNotBlank() ||
                node.path("_type").asText("") == "url"
        }
        if (!looksLikePlaylist) return null

        for (node in nodes) {
            entry(node, host)?.let(entries::add)
            if (title.isBlank()) {
                title = node.path("playlist_title").asText("")
                    .ifBlank { node.path("title").asText("") }
            }
            if (uploader.isBlank()) {
                uploader = node.path("playlist_uploader").asText("")
                    .ifBlank { node.path("playlist_channel").asText("") }
                    .ifBlank { node.path("uploader").asText("") }
                    .ifBlank { node.path("channel").asText("") }
            }
        }
    }

    if (entries.isEmpty()) return null
    return PlaylistListing(
        title = title.ifBlank { "Playlist" },
        uploader = uploader,
        entries = entries.take(limit),
        total = entries.size,
        url = requestedUrl,
    )
}

private fun entry(node: JsonNode, host: String): PlaylistEntry? {
    val webpage = node.path("webpage_url").asText("")
    val direct = node.path("url").asText("")
    val id = node.path("id").asText("")

    val url = when {
        webpage.startsWith("http") -> webpage
        direct.startsWith("http") -> direct
        id.isBlank() -> return null
        isYouTubeHost(host) -> "https://www.youtube.com/watch?v=$id"
        direct.isNotBlank() -> return null
        else -> return null
    }
    val title = node.path("title").asText("").ifBlank {
        node.path("fulltitle").asText("").ifBlank { "Untitled video" }
    }
    return PlaylistEntry(
        url = url,
        title = title,
        durationLabel = clock(node.path("duration").asInt(0)),
    )
}

private fun isYouTubeHost(host: String): Boolean {
    val value = host.lowercase(Locale.US)
    return value.contains("youtube") || value.contains("youtu.be")
}

private fun clock(seconds: Int): String {
    if (seconds <= 0) return ""
    val hours = seconds / 3600
    val minutes = (seconds % 3600) / 60
    val rest = seconds % 60
    return if (hours > 0) {
        String.format(Locale.US, "%d:%02d:%02d", hours, minutes, rest)
    } else {
        String.format(Locale.US, "%d:%02d", minutes, rest)
    }
}

/**
 * Playlist picker: every row describes a rule applied to each video in turn.
 *
 * A short extract of the first playable entry tells us which heights the
 * playlist actually offers; when that probe fails the fixed ladder keeps the
 * feature usable.
 */
fun buildPlaylistSummary(listing: PlaylistListing, sample: MediaSummary?): MediaSummary {
    val count = if (listing.truncated) {
        "${listing.entries.size} of ${listing.total} videos (first ${listing.entries.size})"
    } else {
        "${listing.entries.size} videos"
    }
    return MediaSummary(
        title = listing.title,
        uploader = listing.uploader,
        durationLabel = "$count · downloads one by one",
        thumbnail = sample?.thumbnail,
        webpageUrl = listing.url,
        preview = null,
        choices = buildPlaylistChoices(sample),
    )
}

internal fun buildPlaylistChoices(sample: MediaSummary?): List<FormatChoice> {
    val sampledHeights = sample?.choices.orEmpty()
        .filter { it.height > 0 }
        .map { it.height }
        .distinct()
        .sortedDescending()
    val heights = (sampledHeights.ifEmpty { PLAYLIST_HEIGHTS })
        .filter { it in 144..4320 }
        .take(6)
        .ifEmpty { PLAYLIST_HEIGHTS }

    val hasAudioOnly = sample?.choices.orEmpty().any { it.label.startsWith("Original audio") }

    val rows = mutableListOf<FormatChoice>()
    rows += FormatChoice(
        label = "Best available · MP4",
        detail = "Highest quality every video offers · video + audio",
        formatSelector = "bv*+ba/b",
        mergeTo = "mp4",
    )
    for (height in heights) {
        rows += FormatChoice(
            label = "${height}p · ${qualityName(height)} · MP4",
            detail = "Applied to every video · falls back to a lower quality",
            formatSelector = "bv*[height<=$height]+ba/b[height<=$height]/b",
            mergeTo = "mp4",
            height = height,
        )
    }

    if (hasAudioOnly) {
        rows += FormatChoice(
            label = "Original audio · best",
            detail = "No re-encoding · audio only",
            formatSelector = "ba/b",
        )
    }
    for (profile in PLAYLIST_AUDIO_PROFILES) {
        rows += FormatChoice(
            label = profile.label,
            detail = profile.detail,
            formatSelector = "ba/b",
            extractAudioFormat = profile.ext,
            extractAudioBitrate = profile.bitrate,
        )
    }

    val suggested = rows.firstOrNull { it.height == 1080 }
        ?: rows.firstOrNull { it.height > 0 }
        ?: rows.first()
    return rows.map { if (it === suggested) it.copy(recommended = true) else it }
}

private data class PlaylistAudioProfile(
    val label: String,
    val detail: String,
    val ext: String,
    val bitrate: Int,
)

/** Mirrors the single-video audio profiles, described for a whole playlist. */
private val PLAYLIST_AUDIO_PROFILES = listOf(
    PlaylistAudioProfile("MP3 · 320 kbps", "Audio only · converted per video", "mp3", 320),
    PlaylistAudioProfile("MP3 · 192 kbps", "Audio only · converted per video", "mp3", 192),
    PlaylistAudioProfile("M4A · AAC 256 kbps", "Audio only · converted per video", "m4a", 256),
    PlaylistAudioProfile("FLAC · Lossless", "Audio only · lossless per video", "flac", 0),
    PlaylistAudioProfile("WAV · PCM 16-bit", "Audio only · lossless per video", "wav", 0),
)
