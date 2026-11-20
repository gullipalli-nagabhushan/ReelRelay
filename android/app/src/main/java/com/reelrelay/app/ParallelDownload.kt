package com.reelrelay.app

import android.content.Context
import java.io.File
import java.util.Locale

/**
 * Accelerated path for a format that has direct stream URLs.
 *
 * Downloads the selected video and audio tracks with [SegmentDownloader] and
 * muxes them with the bundled ffmpeg, or converts audio to the chosen profile.
 * Returns `null` when the choice cannot be parallelised, in which case the
 * caller falls back to yt-dlp.
 */
object ParallelDownload {

    suspend fun download(
        context: Context,
        title: String,
        choice: FormatChoice,
        workDir: File,
        meta: AudioMeta? = null,
        onProgress: (Float, Long, String) -> Unit,
    ): File? {
        val video = choice.video
        val audio = choice.audio
        if (video == null && audio == null) return null
        if (choice.mergeTo != null && (video == null || audio == null)) return null

        val base = sanitizeFileName(title)
        val downloader = SegmentDownloader()

        // Audio-only (or audio-from-progressive-video) source.
        if (video == null && audio != null) {
            val sourceLabel = if (choice.extractAudioFormat != null) "video" else "audio"
            val targetFormat = (choice.extractAudioFormat
                ?: audio.extensionOr("m4a")).lowercase(Locale.US)
            val taggable = meta != null && targetFormat in TAGGED_CONTAINERS

            // Keep the original stream untouched when no tags can be written.
            if (choice.extractAudioFormat == null && !taggable) {
                val target = File(workDir, "$base.${audio.extensionOr("m4a")}")
                downloadStream(downloader, audio, target, onProgress, "audio", 0f, 99f)
                onProgress(100f, 0L, "Saved ${target.name}")
                return target
            }

            val source = File(workDir, "$base.source.${audio.extensionOr("m4a")}")
            downloadStream(downloader, audio, source, onProgress, sourceLabel, 0f, 86f)
            try {
                val target = File(workDir, "$base.$targetFormat")
                onProgress(88f, 0L, "Adding tags and cover art…")
                val cover = if (targetFormat in COVER_CONTAINERS) {
                    fetchCover(context, meta, workDir, base)
                } else {
                    null
                }
                val tags = meta?.tags().orEmpty()
                try {
                    embed(context, source, target, targetFormat, audio.codec, choice, tags, cover)
                } catch (t: Throwable) {
                    if (cover == null) throw t
                    // A corrupt or unsupported image must not lose the audio.
                    target.delete()
                    onProgress(92f, 0L, "Cover art could not be embedded — keeping the tags…")
                    embed(context, source, target, targetFormat, audio.codec, choice, tags, null)
                } finally {
                    cover?.delete()
                }
                onProgress(100f, 0L, "Saved ${target.name}")
                return target
            } finally {
                source.delete()
            }
        }

        val videoSource = video ?: return null
        val videoFile = if (audio == null) {
            File(workDir, "$base.${videoSource.extensionOr("mp4")}")
        } else {
            File(workDir, "$base.video.${videoSource.extensionOr("mp4")}")
        }
        downloadStream(
            downloader,
            videoSource,
            videoFile,
            onProgress,
            "video",
            0f,
            if (audio == null) 99f else 84f,
        )
        if (audio == null) {
            onProgress(100f, 0L, "Saved ${videoFile.name}")
            return videoFile
        }

        val audioFile = File(workDir, "$base.audio.${audio.extensionOr("m4a")}")
        downloadStream(downloader, audio, audioFile, onProgress, "audio", 84f, 96f)

        val container = choice.mergeTo ?: "mp4"
        val merged = File(workDir, "$base.$container")
        return try {
            onProgress(97f, 0L, "Merging video and audio…")
            FfmpegRunner.merge(context, videoFile, audioFile, merged, container)
            onProgress(100f, 0L, "Saved ${merged.name}")
            merged
        } finally {
            videoFile.delete()
            audioFile.delete()
        }
    }

    private suspend fun embed(
        context: Context,
        source: File,
        target: File,
        format: String,
        codec: String?,
        choice: FormatChoice,
        tags: Map<String, String>,
        cover: File?,
    ) {
        FfmpegRunner.convertAudio(
            context = context,
            source = source,
            output = target,
            format = format,
            codec = codec,
            bitrate = choice.extractAudioBitrate,
            copyAudio = choice.extractAudioFormat == null,
            tags = tags,
            cover = cover,
        )
    }

    private suspend fun fetchCover(
        context: Context,
        meta: AudioMeta?,
        workDir: File,
        base: String,
    ): File? = CoverArt.fetchFor(context, meta, workDir, base)

    private suspend fun downloadStream(
        downloader: SegmentDownloader,
        source: DownloadSource,
        destination: File,
        onProgress: (Float, Long, String) -> Unit,
        label: String,
        fromPercent: Float,
        toPercent: Float,
    ) {
        val started = System.currentTimeMillis()
        var lastPercent = fromPercent
        downloader.download(
            url = source.url,
            headers = source.headers,
            destination = destination,
            expectedSize = source.size,
        ) { done, total ->
            val fraction = if (total > 0L) done.toDouble() / total else 1.0
            val percent = fromPercent + ((toPercent - fromPercent) * fraction).toFloat()
            // Retries subtract already counted bytes, so hold the high-water mark.
            lastPercent = maxOf(lastPercent, percent.coerceIn(0f, 100f))
            val seconds = (System.currentTimeMillis() - started) / 1000.0
            val speed = if (seconds >= 0.5) done / seconds else 0.0
            val eta = if (speed > 0.0 && total > done) ((total - done) / speed).toLong() else 0L
            val detail = buildString {
                append("Downloading ").append(label)
                append(" · ").append(SegmentDownloader.DEFAULT_CONNECTIONS).append(" connections")
                val rate = formatSpeed(speed)
                if (rate.isNotEmpty()) append(" · ").append(rate)
            }
            onProgress(lastPercent, eta, detail)
        }
    }
}

/** Keep MediaStore and FAT-formatted SD cards happy. */
internal fun sanitizeFileName(raw: String): String {
    val cleaned = raw
        .replace(Regex("[\\\\/:*?\"<>|\\u0000-\\u001f]"), "_")
        .trim()
        .trimEnd('.')
        .take(150)
    return cleaned.ifBlank { "ReelRelay" }
}

internal fun DownloadSource.extensionOr(fallback: String): String =
    ext.ifBlank { fallback }

private fun formatSpeed(bytesPerSecond: Double): String = when {
    bytesPerSecond <= 0.0 -> ""
    bytesPerSecond >= 1024 * 1024 -> String.format(Locale.US, "%.1f MB/s", bytesPerSecond / 1024 / 1024)
    bytesPerSecond >= 1024 -> String.format(Locale.US, "%.0f KB/s", bytesPerSecond / 1024)
    else -> String.format(Locale.US, "%.0f B/s", bytesPerSecond)
}
