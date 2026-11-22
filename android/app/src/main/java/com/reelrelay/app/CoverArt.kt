package com.reelrelay.app

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.ByteArrayOutputStream
import java.io.File
import java.net.URI
import java.util.Locale
import java.util.concurrent.TimeUnit

/**
 * Fetches the video thumbnail and normalises it to JPEG so it can be embedded
 * as cover art.
 *
 * The image yt-dlp saved next to the audio is tried first, so cover art still
 * works when the phone cannot reach `i.ytimg.com`. Remaining candidates are
 * fetched in order. Conversion uses Android's own decoder first (which handles
 * WebP on every supported version) and only falls back to ffmpeg.
 *
 * Cover art stays optional: every failure returns `null` and the audio is still
 * tagged.
 */
object CoverArt {

    private const val MIN_BYTES = 1024L
    private const val MAX_BYTES = 8L * 1024 * 1024
    private const val MAX_EDGE = 1600
    private val IMAGE_EXTENSIONS = setOf("jpg", "jpeg", "png", "webp")

    private val client: OkHttpClient by lazy {
        OkHttpClient.Builder()
            // A thumbnail is cosmetic: never let a slow CDN hold the finished
            // download hostage for long.
            .connectTimeout(6, TimeUnit.SECONDS)
            .readTimeout(8, TimeUnit.SECONDS)
            .callTimeout(10, TimeUnit.SECONDS)
            .followRedirects(true)
            .build()
    }

    /**
     * Cover for [meta], preferring a thumbnail file already in [workDir].
     *
     * @param baseName name of the JPEG to write inside [workDir].
     */
    suspend fun fetchFor(
        context: Context,
        meta: AudioMeta?,
        workDir: File,
        baseName: String,
    ): File? {
        val destination = File(workDir, "$baseName.cover.jpg")
        localImage(workDir)?.let { local ->
            val converted = runCatching {
                normalise(context, local.readBytes(), destination)
            }.getOrNull()
            if (converted != null) return converted
        }
        val candidates = meta?.coverCandidates.orEmpty()
        if (candidates.isEmpty()) return null
        return runCatching {
            fetch(context, candidates, meta?.sourceUrl.orEmpty(), destination)
        }.getOrNull()
    }

    suspend fun fetch(
        context: Context,
        candidates: List<String>,
        referer: String,
        destination: File,
    ): File? = withContext(Dispatchers.IO) {
        if (candidates.isEmpty()) return@withContext null
        val reference = refererFor(referer)
        for (url in expand(candidates)) {
            val bytes = download(url, reference) ?: continue
            val jpeg = normalise(context, bytes, destination)
            if (jpeg != null) return@withContext jpeg
        }
        null
    }

    /** Newest image yt-dlp may have written beside the audio. */
    private fun localImage(workDir: File): File? =
        workDir.listFiles()
            ?.filter { file ->
                file.isFile &&
                    file.length() >= MIN_BYTES &&
                    file.length() <= MAX_BYTES &&
                    file.extension.lowercase(Locale.US) in IMAGE_EXTENSIONS
            }
            ?.maxByOrNull { it.lastModified() }

    private suspend fun normalise(context: Context, bytes: ByteArray, destination: File): File? {
        if (bytes.size < MIN_BYTES || bytes.size > MAX_BYTES) return null
        val jpeg = if (isJpeg(bytes)) bytes else jpegBytes(bytes) ?: ffmpegJpeg(context, bytes)
        if (jpeg == null || jpeg.size < MIN_BYTES) return null
        return runCatching {
            destination.writeBytes(jpeg)
            destination.takeIf { it.isFile && it.length() >= MIN_BYTES }
        }.getOrNull()
    }

    /** Decodes WebP/PNG with the platform decoder and re-encodes as JPEG. */
    private fun jpegBytes(bytes: ByteArray): ByteArray? = runCatching {
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds)
        if (bounds.outWidth <= 0 || bounds.outHeight <= 0) return null
        var sample = 1
        while (bounds.outWidth / (sample * 2) >= MAX_EDGE &&
            bounds.outHeight / (sample * 2) >= MAX_EDGE
        ) {
            sample *= 2
        }
        val options = BitmapFactory.Options().apply { inSampleSize = sample }
        val bitmap = BitmapFactory.decodeByteArray(bytes, 0, bytes.size, options) ?: return null
        val out = ByteArrayOutputStream()
        try {
            if (!bitmap.compress(Bitmap.CompressFormat.JPEG, 90, out)) return null
            out.toByteArray()
        } finally {
            bitmap.recycle()
        }
    }.getOrNull()

    private suspend fun ffmpegJpeg(context: Context, bytes: ByteArray): ByteArray? = runCatching {
        val temp = File.createTempFile("reelrelay-cover-", ".img")
        val converted = File.createTempFile("reelrelay-cover-", ".jpg")
        try {
            temp.writeBytes(bytes)
            FfmpegRunner.toJpeg(context, temp, converted)
            if (converted.isFile && converted.length() > 0L) converted.readBytes() else null
        } finally {
            temp.delete()
            converted.delete()
        }
    }.getOrNull()

    private fun download(url: String, referer: String?): ByteArray? {
        val request = Request.Builder()
            .url(url)
            .header("User-Agent", DEFAULT_USER_AGENT)
            .header("Accept", "image/avif,image/webp,image/apng,image/*,*/*;q=0.8")
            .header("Accept-Language", "en-US,en;q=0.9")
            .apply { if (!referer.isNullOrBlank()) header("Referer", referer) }
            .build()
        return runCatching {
            client.newCall(request).execute().use { response ->
                if (!response.isSuccessful) return@use null
                val body = response.body ?: return@use null
                val length = body.contentLength()
                if (length > MAX_BYTES) return@use null
                val bytes = body.bytes()
                if (bytes.size < MIN_BYTES || bytes.size > MAX_BYTES) return@use null
                val type = response.header("Content-Type").orEmpty().lowercase(Locale.US)
                if (!type.startsWith("image/") && !looksLikeImage(bytes)) return@use null
                bytes
            }
        }.getOrNull()
    }

    /** Some CDNs answer webp only; retry the matching `.jpg` spelling. */
    private fun expand(candidates: List<String>): List<String> {
        val urls = LinkedHashSet<String>()
        for (url in candidates) {
            if (url.isBlank()) continue
            urls += url
            val host = runCatching { URI(url).host.orEmpty() }.getOrDefault("")
            val ytImage = host.contains("ytimg.com") || host.contains("ggpht.com")
            if (ytImage && url.endsWith(".webp", ignoreCase = true)) {
                urls += url.dropLast(5) + ".jpg"
            }
        }
        return urls.toList()
    }

    private fun refererFor(pageUrl: String): String? {
        val uri = runCatching { URI(pageUrl) }.getOrNull() ?: return null
        val host = uri.host ?: return null
        val scheme = uri.scheme ?: "https"
        return "$scheme://$host/"
    }

    private fun isJpeg(bytes: ByteArray): Boolean =
        bytes.size > 2 && bytes[0] == 0xFF.toByte() && bytes[1] == 0xD8.toByte()

    private fun looksLikeImage(bytes: ByteArray): Boolean {
        if (bytes.size < 12) return false
        val png = bytes[0] == 0x89.toByte() && bytes[1] == 0x50.toByte()
        val riff = bytes[0] == 0x52.toByte() && bytes[1] == 0x49.toByte()
        val gif = bytes[0] == 0x47.toByte() && bytes[1] == 0x49.toByte()
        return png || riff || gif
    }
}
