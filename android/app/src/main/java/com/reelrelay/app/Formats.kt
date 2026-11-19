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

/** One row in the format picker. */
data class FormatChoice(
    val label: String,
    val formatSelector: String,
    val mergeToMp4: Boolean = false,
    val extractAudioFormat: String? = null,
    val video: DownloadSource? = null,
    val audio: DownloadSource? = null,
)

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

private fun isMissing(codec: String?): Boolean = codec.isNullOrBlank() || codec == "none"

private fun displayHeight(format: VideoFormat): Int {
    val width = format.width
    val height = format.height
    return if (width > 0 && height > 0) min(width, height) else height
}

private fun estimatedBytes(format: VideoFormat, durationSeconds: Int): Long {
    if (format.fileSize > 0) return format.fileSize
    if (format.fileSizeApproximate > 0) return format.fileSizeApproximate
    val bitrate = when {
        format.tbr > 0 -> format.tbr.toLong()
        format.abr > 0 -> format.abr.toLong()
        else -> 0L
    }
    return if (bitrate > 0 && durationSeconds > 0) bitrate * 1000 / 8 * durationSeconds else 0L
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

private fun bestVideoAtHeight(formats: List<VideoFormat>): VideoFormat? {
    return formats.sortedWith(
        compareByDescending<VideoFormat> { it.ext == "mp4" }
            .thenByDescending { it.vcodec?.startsWith("avc") == true }
            .thenByDescending { it.tbr }
    ).firstOrNull()
}

private fun bestAudio(formats: List<VideoFormat>): VideoFormat? {
    return formats
        .filter {
            isMissing(it.vcodec) && !isMissing(it.acodec) && (it.abr > 0 || it.tbr > 0) &&
                !it.url.isNullOrBlank()
        }
        .maxByOrNull { if (it.abr > 0) it.abr else it.tbr }
}

private fun bestProgressive(formats: List<VideoFormat>): VideoFormat? {
    // Smallest watchable progressive stream keeps data use and start-up low.
    val candidates = formats.filter {
        !isMissing(it.vcodec) && !isMissing(it.acodec) &&
            it.height > 0 && !it.url.isNullOrBlank() &&
            (it.ext ?: "mp4") in PLAYABLE_EXTENSIONS
    }
    if (candidates.isEmpty()) return null
    val sorted = candidates.sortedWith(
        compareBy<VideoFormat> { displayHeight(it) }.thenByDescending { it.ext == "mp4" }
    )
    return sorted.firstOrNull { displayHeight(it) >= 360 } ?: sorted.last()
}

private fun previewHeaders(format: VideoFormat, info: VideoInfo): Map<String, String> {
    val merged = LinkedHashMap<String, String>()
    info.httpHeaders?.forEach { (key, value) -> merged[key] = value }
    format.httpHeaders?.forEach { (key, value) -> merged[key] = value }
    merged.putIfAbsent(
        "User-Agent",
        "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 " +
            "(KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36"
    )
    return merged
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
        headers = previewHeaders(format, info),
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

/** Turn a raw yt-dlp payload into the small model the UI renders. */
fun buildSummary(info: VideoInfo): MediaSummary {
    val formats = info.formats.orEmpty().toList()
    val duration = info.duration
    val playableVideo = formats.filter { !isMissing(it.vcodec) && it.height > 0 && !it.url.isNullOrBlank() }
    val audio = bestAudio(formats)

    val videoChoices = mutableListOf<FormatChoice>()
    val heights = playableVideo.map(::displayHeight).filter { it > 0 }.distinct().sortedDescending()
    for (height in heights) {
        val group = playableVideo.filter { displayHeight(it) == height }
        val progressive = group
            .filter { !isMissing(it.acodec) }
            .sortedWith(compareByDescending<VideoFormat> { it.ext == "mp4" }.thenByDescending { it.tbr })
            .firstOrNull()
        val videoOnly = group.filter { isMissing(it.acodec) }
        val video = bestVideoAtHeight(videoOnly) ?: progressive
        if (video == null) continue

        val selector: String
        val merge: Boolean
        val bytes: Long
        if (!isMissing(video.acodec)) {
            // Progressive file: audio already inside the video.
            val id = video.formatId ?: continue
            selector = id
            merge = false
            bytes = estimatedBytes(video, duration)
        } else if (audio != null) {
            val videoId = video.formatId ?: continue
            val audioId = audio.formatId ?: continue
            selector = "$videoId+$audioId"
            merge = true
            bytes = estimatedBytes(video, duration) + estimatedBytes(audio, duration)
        } else {
            // Video-only stream with no audio source: not a useful download.
            continue
        }

        val extension = if (merge) "mp4" else (video.ext ?: "mp4")
        val size = humanSize(bytes)
        val label = buildString {
            append("Video · ${height}p · ${extension.uppercase()}")
            if (size.isNotEmpty()) append(" · ~$size")
        }
        val videoSource = downloadSource(video, info, duration)
        val audioSource = audio?.let { downloadSource(it, info, duration) }
        videoChoices.add(
            FormatChoice(
                label = label,
                formatSelector = selector,
                mergeToMp4 = merge,
                video = videoSource,
                audio = if (merge) audioSource else null,
            )
        )
    }

    val audioChoices = buildList {
        val audioSource = audio?.let { downloadSource(it, info, duration) }
        if (audio != null) {
            add(
                FormatChoice(
                    "Audio · original stream (${(audio.ext ?: "m4a").uppercase()})",
                    "bestaudio/best",
                    audio = audioSource,
                )
            )
        }
        add(
            FormatChoice(
                "Audio · MP3 320 kbps",
                "bestaudio/best",
                extractAudioFormat = "mp3",
                audio = audioSource,
            )
        )
        add(
            FormatChoice(
                "Audio · M4A 256 kbps",
                "bestaudio/best",
                extractAudioFormat = "m4a",
                audio = audioSource,
            )
        )
    }

    val progressive = bestProgressive(formats)
    val preview = progressive?.let {
        PreviewSource(
            url = it.url!!,
            headers = previewHeaders(it, info),
            label = "${displayHeight(it)}p ${(it.ext ?: "mp4").uppercase()}",
        )
    }

    val metaBits = buildList {
        val durationText = durationLabel(duration)
        if (durationText.isNotEmpty()) add(durationText)
        val platform = info.extractor?.replaceFirstChar { char -> char.uppercase() }
        if (!platform.isNullOrBlank()) add(platform)
        playableVideo.maxOfOrNull(::displayHeight)?.let { add("up to ${it}p") }
    }

    return MediaSummary(
        title = info.title ?: info.fulltitle ?: "Untitled",
        uploader = info.uploader ?: info.extractor ?: "",
        durationLabel = metaBits.joinToString(" · "),
        thumbnail = info.thumbnail ?: info.thumbnails?.lastOrNull()?.url,
        webpageUrl = info.webpageUrl ?: "",
        preview = preview,
        choices = videoChoices + audioChoices,
    )
}
