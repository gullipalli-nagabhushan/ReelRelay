package com.reelrelay.app

import com.yausername.youtubedl_android.mapper.VideoFormat
import com.yausername.youtubedl_android.mapper.VideoInfo
import java.util.Locale
import kotlin.math.min

/** A direct media URL that can be fetched with parallel range requests. */
data class DownloadSource(
    val url: String,
    val headers: Map<String, String>,
    val formatId: String,
    val ext: String,
    val size: Long,
    val codec: String?,
)

/**
 * One row in the format picker.
 *
 * [label] is the bold first line ("1080p · H.264 · MP4") and [detail] is the
 * quieter second line ("Full HD · 60 fps · ~45 MB"). [mergeTo] is the container
 * ffmpeg should produce when a separate video and audio stream are muxed.
 */
data class FormatChoice(
    val label: String,
    val detail: String = "",
    val formatSelector: String,
    val mergeTo: String? = null,
    val extractAudioFormat: String? = null,
    val extractAudioBitrate: Int = 0,
    val video: DownloadSource? = null,
    val audio: DownloadSource? = null,
    val recommended: Boolean = false,
    /** Display height for video rows, `0` for audio. */
    val height: Int = 0,
) {
    fun selectionLabel(): String = if (recommended) "★ $label" else label

    /**
     * `AutoCompleteTextView` writes `toString()` back into the field after a
     * tap, so it must be the readable title rather than the data-class dump.
     */
    override fun toString(): String = selectionLabel()
}

/** Everything the preview card needs, already flattened for the UI. */
data class MediaSummary(
    val title: String,
    val uploader: String,
    val durationLabel: String,
    val thumbnail: String?,
    val webpageUrl: String,
    val preview: PreviewSource?,
    val choices: List<FormatChoice>,
)

/** A progressive stream the phone can play directly with ExoPlayer. */
data class PreviewSource(
    val url: String,
    val headers: Map<String, String>,
    val label: String,
)

private val PLAYABLE_EXTENSIONS = setOf("mp4", "m4v", "webm", "mov")
private val DEFAULT_USER_AGENT =
    "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 " +
        "(KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36"

private val PLATFORM_ORIGINS = mapOf(
    "youtube" to "https://www.youtube.com/",
    "instagram" to "https://www.instagram.com/",
    "facebook" to "https://www.facebook.com/",
)

private data class Family(val label: String, val container: String)

private val FAMILIES = linkedMapOf(
    "h264" to Family("H.264", "mp4"),
    "h265" to Family("H.265", "mp4"),
    "av1" to Family("AV1", "mp4"),
    "vp9" to Family("VP9", "webm"),
)

private fun isMissing(codec: String?): Boolean = codec.isNullOrBlank() || codec == "none"

private fun displayHeight(format: VideoFormat): Int {
    val width = format.width
    val height = format.height
    return if (width > 0 && height > 0) min(width, height) else height
}

private fun isVertical(format: VideoFormat): Boolean =
    format.width > 0 && format.height > 0 && format.height > format.width

private fun bitrate(format: VideoFormat): Long =
    when {
        format.tbr > 0 -> format.tbr.toLong()
        format.abr > 0 -> format.abr.toLong()
        format.asr > 0 -> format.asr.toLong()
        else -> 0L
    }

private fun estimatedBytes(format: VideoFormat, durationSeconds: Int): Long {
    if (format.fileSize > 0) return format.fileSize
    if (format.fileSizeApproximate > 0) return format.fileSizeApproximate
    val rate = bitrate(format)
    return if (rate > 0 && durationSeconds > 0) rate * 1000 / 8 * durationSeconds else 0L
}

private fun humanSize(bytes: Long): String {
    if (bytes <= 0) return ""
    val units = arrayOf("B", "KB", "MB", "GB", "TB")
    var value = bytes.toDouble()
    var unit = 0
    while (value >= 1024 && unit < units.lastIndex) {
        value /= 1024
        unit++
    }
    return if (unit == 0) {
        "${value.toInt()} ${units[unit]}"
    } else {
        String.format(Locale.US, "%.1f %s", value, units[unit])
    }
}

private fun durationLabel(seconds: Int): String {
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

internal fun qualityName(height: Int): String = when (height) {
    4320 -> "8K"
    2160 -> "4K"
    1440 -> "2K"
    1080 -> "Full HD"
    720 -> "HD"
    480 -> "SD"
    360 -> "SD"
    240 -> "Low"
    144 -> "Low"
    else -> "${height}p"
}

private fun codecFamily(codec: String?): String {
    val value = codec?.lowercase(Locale.US).orEmpty()
    return when {
        value.startsWith("avc") || value.startsWith("h264") -> "h264"
        value.startsWith("hev") || value.startsWith("hvc") || value.startsWith("h265") -> "h265"
        value.startsWith("av01") || value.startsWith("av1") -> "av1"
        value.startsWith("vp9") || value.startsWith("vp09") || value.startsWith("vp8") -> "vp9"
        else -> "other"
    }
}

private fun isAudioOnly(format: VideoFormat): Boolean =
    isMissing(format.vcodec) && !isMissing(format.acodec) &&
        bitrate(format) > 0 && !format.url.isNullOrBlank()

private fun isProgressive(format: VideoFormat): Boolean =
    !isMissing(format.vcodec) && !isMissing(format.acodec) && displayHeight(format) > 0

private fun isUsable(format: VideoFormat): Boolean {
    val note = format.formatNote?.lowercase(Locale.US).orEmpty()
    if (note.contains("storyboard") || note.contains("drm")) return false
    return !format.url.isNullOrBlank() || !format.manifestUrl.isNullOrBlank()
}

private fun isHdr(format: VideoFormat): Boolean {
    val text = listOfNotNull(format.formatNote, format.format)
        .joinToString(" ")
        .lowercase(Locale.US)
    return text.contains("hdr") || text.contains("hlg") || text.contains("pq")
}

private fun fpsOf(format: VideoFormat): Int = format.fps

private fun audioCodecName(format: VideoFormat): String {
    val codec = format.acodec?.lowercase(Locale.US).orEmpty()
    return when {
        codec.startsWith("mp4a") || codec.startsWith("aac") -> "AAC"
        codec.startsWith("opus") -> "Opus"
        codec.startsWith("vorbis") -> "Vorbis"
        codec.startsWith("mp3") -> "MP3"
        codec.startsWith("flac") -> "FLAC"
        else -> "audio"
    }
}

private class CandidateRank(val direct: Int, val container: Int, val bitrate: Long) :
    Comparable<CandidateRank> {
    override fun compareTo(other: CandidateRank): Int =
        compareValuesBy(this, other, { it.direct }, { it.container }, { it.bitrate })
}

private fun candidateRank(format: VideoFormat, family: String): CandidateRank {
    val direct = if (isDirect(format)) 0 else 1
    val ext = format.ext.orEmpty()
    val wanted = FAMILIES[family]?.container
    val container = if (wanted != null && ext == wanted) 0 else 1
    return CandidateRank(direct, container, -bitrate(format))
}

private fun isDirect(format: VideoFormat): Boolean =
    !format.url.isNullOrBlank() && !isManifestUrl(format.url.orEmpty()) &&
        format.manifestUrl.isNullOrBlank()

private fun bestAudioOnly(formats: List<VideoFormat>): VideoFormat? =
    formats.filter(::isAudioOnly).maxByOrNull { bitrate(it) }

private fun bestAudioFor(formats: List<VideoFormat>, container: String): VideoFormat? {
    val candidates = formats.filter(::isAudioOnly)
    if (candidates.isEmpty()) return null
    val preferredExt = if (container == "mp4") "m4a" else "webm"
    val preferred = candidates.filter { it.ext == preferredExt }
    return (preferred.ifEmpty { candidates }).maxByOrNull { bitrate(it) }
}

private fun bestProgressive(format: List<VideoFormat>): VideoFormat? =
    format.filter(::isProgressive).maxByOrNull { displayHeight(it) * 1_000_000L + bitrate(it) }

private fun bestProgressiveWatchable(formats: List<VideoFormat>): VideoFormat? {
    val candidates = formats.filter {
        isProgressive(it) && !it.url.isNullOrBlank() &&
            (it.ext ?: "mp4") in PLAYABLE_EXTENSIONS
    }
    if (candidates.isEmpty()) return null
    val sorted = candidates.sortedWith(
        compareBy<VideoFormat> { displayHeight(it) }.thenByDescending { it.ext == "mp4" }
    )
    return sorted.firstOrNull { displayHeight(it) >= 360 } ?: sorted.last()
}

/** Platform-specific headers, mirroring what the desktop app sends. */
internal fun streamHeaders(format: VideoFormat, info: VideoInfo): Map<String, String> {
    val source = LinkedHashMap<String, String>()
    info.httpHeaders?.forEach { (key, value) -> source[key.lowercase(Locale.US)] = value }
    format.httpHeaders?.forEach { (key, value) -> source[key.lowercase(Locale.US)] = value }

    val platform = platformOf(info)
    val origin = PLATFORM_ORIGINS[platform]
    val merged = LinkedHashMap<String, String>()
    merged["User-Agent"] = source["user-agent"] ?: DEFAULT_USER_AGENT
    if (origin != null) {
        merged["Referer"] = source["referer"] ?: origin
        merged["Origin"] = origin.trimEnd('/')
    }
    merged["Accept-Language"] = source["accept-language"] ?: "en-US,en;q=0.9"
    source["cookie"]?.let { merged["Cookie"] = it }
    source.forEach { (key, value) ->
        if (merged.keys.none { it.equals(key, ignoreCase = true) }) {
            merged[key] = value
        }
    }
    return merged
}

private fun platformOf(info: VideoInfo): String? {
    val extractor = info.extractorKey ?: info.extractor
    val haystack = listOfNotNull(extractor, info.webpageUrl).joinToString(" ").lowercase(Locale.US)
    return when {
        haystack.contains("instagram") -> "instagram"
        haystack.contains("facebook") || haystack.contains("fb.watch") -> "facebook"
        haystack.contains("youtube") || haystack.contains("youtu.be") -> "youtube"
        else -> null
    }
}

/**
 * A direct source for the parallel downloader, or `null` when yt-dlp must
 * handle the stream (manifests, missing URLs, unknown sizes).
 */
private fun downloadSource(
    format: VideoFormat,
    info: VideoInfo,
    duration: Int,
): DownloadSource? {
    val url = format.url
    if (url.isNullOrBlank() || isManifestUrl(url) || !format.manifestUrl.isNullOrBlank()) {
        return null
    }
    val codec = format.acodec?.takeIf { !isMissing(it) }
        ?: format.vcodec?.takeIf { !isMissing(it) }
    return DownloadSource(
        url = url,
        headers = streamHeaders(format, info),
        formatId = format.formatId.orEmpty(),
        ext = format.ext ?: "mp4",
        size = estimatedBytes(format, duration),
        codec = codec,
    )
}

private fun isManifestUrl(url: String): Boolean {
    val path = url.substringBefore('?').lowercase(Locale.US)
    return path.endsWith(".m3u8") || path.endsWith(".mpd") || path.endsWith(".ism")
}

private fun detailLine(vararg parts: String?): String =
    parts.filter { !it.isNullOrBlank() }.joinToString(" · ")

private fun sizeLabel(bytes: Long): String? =
    humanSize(bytes).takeIf { it.isNotEmpty() }?.let { "~$it" }

/** Turn a raw yt-dlp payload into the small model the UI renders. */
fun buildSummary(info: VideoInfo): MediaSummary {
    val formats = info.formats.orEmpty().toList()
    val duration = info.duration

    val usableVideo = formats.filter {
        isUsable(it) && !isMissing(it.vcodec) && displayHeight(it) > 0
    }
    val audioOnly = bestAudioOnly(formats)
    val progressive = bestProgressive(formats)

    val videoChoices = buildVideoChoices(info, formats, usableVideo, duration)
    val audioChoices = buildAudioChoices(info, formats, audioOnly, progressive, duration)

    val recommendedIndex = videoChoices.indexOfFirst { it.recommended }
    val orderedVideo = if (recommendedIndex > 0) {
        listOf(videoChoices[recommendedIndex]) +
            videoChoices.filterIndexed { index, _ -> index != recommendedIndex }
    } else {
        videoChoices
    }

    val previewFormat = bestProgressiveWatchable(formats)
    val preview = previewFormat?.let {
        PreviewSource(
            url = it.url!!,
            headers = streamHeaders(it, info),
            label = "${displayHeight(it)}p ${(it.ext ?: "mp4").uppercase(Locale.US)}",
        )
    }

    val metaBits = buildList {
        val durationText = durationLabel(duration)
        if (durationText.isNotEmpty()) add(durationText)
        val platform = info.extractor?.replaceFirstChar { char -> char.uppercase() }
        if (!platform.isNullOrBlank()) add(platform)
        usableVideo.map(::displayHeight).maxOrNull()?.let { add("up to ${it}p") }
    }

    return MediaSummary(
        title = info.title ?: info.fulltitle ?: "Untitled",
        uploader = info.uploader ?: info.extractor ?: "",
        durationLabel = metaBits.joinToString(" · "),
        thumbnail = info.thumbnail ?: info.thumbnails?.lastOrNull()?.url,
        webpageUrl = info.webpageUrl ?: "",
        preview = preview,
        choices = orderedVideo + audioChoices,
    )
}

private fun buildVideoChoices(
    info: VideoInfo,
    formats: List<VideoFormat>,
    usableVideo: List<VideoFormat>,
    duration: Int,
): List<FormatChoice> {
    val choices = mutableListOf<FormatChoice>()
    val heights = usableVideo.map(::displayHeight).filter { it > 0 }.distinct().sortedDescending()

    for (height in heights) {
        val group = usableVideo.filter { displayHeight(it) == height }
        val vertical = group.any(::isVertical)

        val byFamily = LinkedHashMap<String, VideoFormat>()
        for (format in group) {
            if (!isMissing(format.acodec)) continue
            val family = codecFamily(format.vcodec)
            if (family !in FAMILIES) continue
            val current = byFamily[family]
            if (current == null ||
                candidateRank(format, family) < candidateRank(current, family)
            ) {
                byFamily[family] = format
            }
        }

        var madeForHeight = 0
        for ((family, meta) in FAMILIES) {
            if (madeForHeight >= 3) break
            val video = byFamily[family] ?: continue
            val container = if (family == "vp9" && video.ext != "webm") "mp4" else meta.container
            val audio = bestAudioFor(formats, container) ?: continue
            val videoId = video.formatId ?: continue
            val audioId = audio.formatId ?: continue

            val fps = fpsOf(video)
            val hdr = isHdr(video)
            val totalBytes = estimatedBytes(video, duration) + estimatedBytes(audio, duration)
            val detail = detailLine(
                qualityName(height),
                if (fps >= 50) "$fps fps" else null,
                if (hdr) "HDR" else null,
                if (vertical) "Portrait" else null,
                sizeLabel(totalBytes),
            )
            choices += FormatChoice(
                label = "${height}p · ${meta.label} · ${container.uppercase(Locale.US)}",
                detail = detailLine(detail, "${meta.label} video + ${audioCodecName(audio)} audio"),
                formatSelector = "$videoId+$audioId",
                mergeTo = container,
                video = downloadSource(video, info, duration),
                audio = downloadSource(audio, info, duration),
                height = height,
            )
            madeForHeight++
        }

        if (madeForHeight > 0) continue

        // Instagram and Facebook usually expose one progressive file with audio.
        val prog = group.filter(::isProgressive)
            .maxByOrNull { bitrate(it) }
            ?: continue
        val id = prog.formatId ?: continue
        val ext = prog.ext ?: "mp4"
        val family = codecFamily(prog.vcodec)
        val codecLabel = FAMILIES[family]?.label ?: "Video"
        val fps = fpsOf(prog)
        val bytes = estimatedBytes(prog, duration)
        choices += FormatChoice(
            label = "${height}p · $codecLabel · ${ext.uppercase(Locale.US)}",
            detail = detailLine(
                qualityName(height),
                if (fps >= 50) "$fps fps" else null,
                if (isHdr(prog)) "HDR" else null,
                if (vertical) "Portrait" else null,
                sizeLabel(bytes),
                "Single file with audio",
            ),
            formatSelector = id,
            video = downloadSource(prog, info, duration),
            height = height,
        )
    }

    if (choices.isEmpty()) return choices

    // Prefer 1080p H.264/MP4, the broadest-compatibility option. Sites that
    // only expose progressive files (Instagram, Facebook) keep their highest
    // quality row as the suggestion.
    val best = choices.firstOrNull { it.label.startsWith("1080p") && it.mergeTo == "mp4" }
        ?: choices.firstOrNull { it.mergeTo == "mp4" }
        ?: choices.first()

    return choices.map { choice ->
        if (choice === best) choice.copy(recommended = true) else choice
    }
}

private data class AudioProfile(
    val label: String,
    val ext: String,
    val bitrate: Int,
    val lossless: Boolean = false,
)

private val AUDIO_PROFILES = listOf(
    AudioProfile("MP3 · 320 kbps", "mp3", 320),
    AudioProfile("MP3 · 192 kbps", "mp3", 192),
    AudioProfile("M4A · AAC 256 kbps", "m4a", 256),
    AudioProfile("FLAC · Lossless", "flac", 0, lossless = true),
    AudioProfile("WAV · PCM 16-bit", "wav", 1411, lossless = true),
)

private fun buildAudioChoices(
    info: VideoInfo,
    formats: List<VideoFormat>,
    audioOnly: VideoFormat?,
    progressive: VideoFormat?,
    duration: Int,
): List<FormatChoice> {
    // Instagram/Facebook often have no audio-only stream, so conversion takes
    // the audio track out of the progressive video instead.
    val source = audioOnly ?: progressive ?: return emptyList()
    val fromVideo = audioOnly == null
    val sourceDownload = downloadSource(source, info, duration)
    val sourceExt = source.ext ?: if (fromVideo) "mp4" else "m4a"
    val sourceSelector = source.formatId ?: if (fromVideo) "best" else "bestaudio"

    val choices = mutableListOf<FormatChoice>()

    if (!fromVideo) {
        val kbps = bitrate(source)
        choices += FormatChoice(
            label = "Original audio · ${sourceExt.uppercase(Locale.US)}",
            detail = detailLine(
                if (kbps > 0) "$kbps kbps" else null,
                sizeLabel(estimatedBytes(source, duration)),
                "No re-encoding",
            ),
            formatSelector = sourceSelector,
            audio = sourceDownload,
        )
    }

    for (profile in AUDIO_PROFILES) {
        val estimated = when {
            profile.ext == "wav" && duration > 0 -> duration.toLong() * 44100 * 2 * 2
            profile.lossless && duration > 0 -> duration.toLong() * 200_000
            profile.bitrate > 0 && duration > 0 -> duration.toLong() * profile.bitrate * 1000 / 8
            else -> 0L
        }
        choices += FormatChoice(
            label = profile.label,
            detail = detailLine(
                sizeLabel(estimated),
                if (fromVideo) "Extracted from the video" else "Converted on the phone",
            ),
            formatSelector = sourceSelector,
            extractAudioFormat = profile.ext,
            // Lossless targets ignore a bitrate; keep it zero so yt-dlp does
            // not receive a meaningless "-audio-quality".
            extractAudioBitrate = if (profile.lossless) 0 else profile.bitrate,
            audio = sourceDownload,
        )
    }

    return choices
}
