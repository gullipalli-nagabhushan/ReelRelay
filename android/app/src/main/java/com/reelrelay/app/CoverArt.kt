package com.reelrelay.app

import android.content.Context
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File
import java.net.URI
import java.util.Locale
import java.util.concurrent.TimeUnit

/**
 * Fetches the video thumbnail and normalises it to JPEG so it can be embedded
 * as cover art.
 *
 * Candidates are tried in order, so a missing `maxresdefault.jpg` falls back to
 * the smaller YouTube thumbnails and then to whatever yt-dlp reported. Cover art
 * is optional: every failure returns `null` and the audio is still tagged.
 */
object CoverArt {

    private const val MIN_BYTES = 1024L
    private const val MAX_BYTES = 8L * 1024 * 1024

    private val client: OkHttpClient by lazy {
        OkHttpClient.Builder()
            .connectTimeout(15, TimeUnit.SECONDS)
            .readTimeout(25, TimeUnit.SECONDS)
            .followRedirects(true)
            .build()
    }

    /** Convenience overload for an [AudioMeta] and a work-directory name. */
    suspend fun fetchFor(
        context: Context,
        meta: AudioMeta?,
        workDir: File,
        baseName: String,
    ): File? {
        val candidates = meta?.coverCandidates.orEmpty()
        if (candidates.isEmpty()) return null
        return runCatching {
            fetch(context, candidates, meta?.sourceUrl.orEmpty(), File(workDir, "$baseName.cover.jpg"))
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
            val jpeg = if (isJpeg(bytes)) {
                destination.writeBytes(bytes)
                destination
            } else {
                convert(context, bytes, destination)
            }
            if (jpeg != null && jpeg.isFile && jpeg.length() >= MIN_BYTES) {
                return@withContext jpeg
            }
            jpeg?.delete()
        }
        null
    }

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

    private suspend fun convert(context: Context, bytes: ByteArray, destination: File): File? {
        val parent = destination.parentFile ?: return null
        val temp = File(parent, "${destination.nameWithoutExtension}.source")
        val converted = File(parent, "${destination.nameWithoutExtension}.converted.jpg")
        return try {
            temp.writeBytes(bytes)
            converted.delete()
            FfmpegRunner.toJpeg(context, temp, converted)
            if (!converted.isFile || converted.length() <= 0L) return null
            converted.copyTo(destination, overwrite = true)
            converted.delete()
            destination
        } catch (t: Throwable) {
            null
        } finally {
            temp.delete()
            converted.delete()
        }
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
