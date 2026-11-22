package com.reelrelay.app

import android.content.ContentValues
import android.content.Context
import android.media.MediaScannerConnection
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import android.util.Log
import androidx.annotation.RequiresApi
import com.yausername.youtubedl_android.YoutubeDL
import com.yausername.youtubedl_android.YoutubeDLRequest
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withContext
import java.io.File
import java.util.Locale

/**
 * Runs yt-dlp on the device and returns the produced file.
 *
 * The download is written to app-private cache first, then [MediaPublisher]
 * copies it into the public Downloads collection. A dedicated work directory
 * per job keeps concurrent or cancelled downloads from mixing files.
 */
class DownloadRepository(private val context: Context) {

    suspend fun download(
        url: String,
        title: String,
        choice: FormatChoice,
        processId: String,
        cookiesPath: String? = null,
        meta: AudioMeta? = null,
        onProgress: (Float, Long, String) -> Unit,
    ): File = withContext(Dispatchers.IO) {
        val workDir = File(context.cacheDir, "reelrelay-$processId")
        if (!workDir.exists() && !workDir.mkdirs()) {
            throw IllegalStateException("Could not create a temporary download folder.")
        }
        // One monotonic reporter for both paths: the bar never walks backwards,
        // even when the accelerated attempt fails and yt-dlp takes over.
        val progress = ProgressSink(onProgress)
        try {
            val accelerated = try {
                ParallelDownload.download(context, title, choice, workDir, progress::report)
            } catch (cancel: CancellationException) {
                throw cancel
            } catch (t: Throwable) {
                Log.i(TAG, "Parallel download unavailable, falling back to yt-dlp", t)
                null
            }
            if (accelerated != null && accelerated.isFile && accelerated.length() > 0L) {
                // Tags and artwork are always added here, never by the path that
                // produced the file: an accelerated download and a yt-dlp
                // download of the same song must be byte-compatible in metadata.
                val finished = if (meta != null && isAudioFile(accelerated)) {
                    finalizeAudio(meta, workDir, accelerated, progress)
                } else {
                    accelerated
                }
                progress.report(97f, 0L, "Finishing ${finished.name}…")
                return@withContext finished
            }
            accelerated?.delete()
            workDir.listFiles()?.forEach { it.delete() }
            progress.beginFallback()
            runYtDlp(url, choice, workDir, processId, cookiesPath, meta, progress)
        } catch (t: Throwable) {
            workDir.deleteRecursively()
            throw t
        }
    }

    /**
     * Enumerates a playlist without downloading anything.
     *
     * `--flat-playlist --dump-json` emits one JSON object per entry and keeps
     * this to a single network round trip per page of entries. `null` means the
     * link is not a readable playlist.
     */
    suspend fun listPlaylist(
        url: String,
        cookiesPath: String?,
    ): PlaylistListing? = withContext(Dispatchers.IO) {
        val request = YoutubeDLRequest(url).apply {
            addOption("--flat-playlist")
            // One JSON object per entry. `--dump-json` also lets the library
            // tolerate a partially unavailable playlist, which `-J` cannot.
            addOption("--dump-json")
            addOption("--no-warnings")
            addOption("--ignore-errors")
            if (!cookiesPath.isNullOrBlank()) addOption("--cookies", cookiesPath)
        }
        // An empty id tells youtubedl-android to return output even when a few
        // entries failed; the listing is still useful.
        val response = YoutubeDL.getInstance().execute(request, "")
        parsePlaylist(response.out, url)
    }

    /** Original single-connection path, kept as the universal fallback. */
    private suspend fun runYtDlp(
        url: String,
        choice: FormatChoice,
        workDir: File,
        processId: String,
        cookiesPath: String?,
        meta: AudioMeta?,
        progress: ProgressSink,
    ): File {
        val request = YoutubeDLRequest(url)
        request.addOption("--no-playlist")
        request.addOption("--no-mtime")
        request.addOption("--newline")
        request.addOption("--no-part")
        request.addOption("-o", File(workDir, "%(title).150B.%(ext)s").absolutePath)
        request.addOption("-f", choice.formatSelector)
        if (!cookiesPath.isNullOrBlank()) {
            request.addOption("--cookies", cookiesPath)
        }

        choice.mergeTo?.let { container ->
            request.addOption("--merge-output-format", container)
        }
        choice.extractAudioFormat?.let { format ->
            request.addOption("--extract-audio")
            request.addOption("--audio-format", format)
            when {
                choice.extractAudioBitrate > 0 ->
                    request.addOption("--audio-quality", "${choice.extractAudioBitrate}K")
                format == "flac" || format == "wav" -> Unit
                else -> request.addOption("--audio-quality", "0")
            }
        }

        val audioOnly = choice.extractAudioFormat != null ||
            (choice.video == null && choice.mergeTo == null && choice.height == 0)
        // The Kotlin writer below adds tags (and artwork) without re-encoding.
        // Letting yt-dlp run its own metadata pass as well meant a second
        // ffmpeg remux at the end of every download, which is slow and can
        // silently drop the picture on some device builds.
        val targetFormat = (choice.extractAudioFormat ?: choice.audio?.ext ?: "m4a")
            .lowercase(Locale.US)
        val localTagger = meta != null && AudioTagger.supports(targetFormat)
        if (meta != null && audioOnly) {
            if (!localTagger) request.addOption("--add-metadata")
            // Keep a copy of the thumbnail beside the audio: it is a reliable
            // cover source even when the phone cannot reach the image CDN.
            request.addOption("--write-thumbnail")
        }

        // yt-dlp sweeps 0→100 for every stream it fetches, which made the bar
        // fill once for video and again for audio. Map each stream into its own
        // slice of the overall bar instead.
        val stages = if (choice.mergeTo != null) 2 else 1
        var stage = 0
        var lastRaw = 0f
        val execute = runCatching {
            YoutubeDL.getInstance().execute(request, processId) { value, eta, line ->
                val raw = value.coerceIn(0f, 100f)
                if (raw + 20f < lastRaw) {
                    stage = (stage + 1).coerceAtMost(stages - 1)
                }
                lastRaw = raw
                val span = 92f / stages
                progress.report(stage * span + raw / 100f * span, eta, line ?: "")
            }
        }

        val produced = newestMediaFile(workDir)
        execute.exceptionOrNull()?.let { failure ->
            currentCoroutineContext().ensureActive()
            // Post-processing (metadata, cover art) can fail after a good file
            // was written; keep the audio instead of discarding the download.
            val recoverable = failure.message.orEmpty()
                .contains("postprocessing", ignoreCase = true)
            if (produced == null || !recoverable) throw failure
            Log.w(TAG, "yt-dlp post-processing failed; using the downloaded file", failure)
        }
        if (produced == null) {
            throw IllegalStateException("The download finished but no file was produced.")
        }
        val finished = if (meta != null && audioOnly) {
            finalizeAudio(meta, workDir, produced, progress)
        } else {
            produced
        }
        progress.report(97f, 0L, "Finishing ${finished.name}…")
        return finished
    }

    /**
     * Adds our tags and cover art to an audio file yt-dlp produced.
     *
     * MP3/FLAC/M4A are rewritten in place by [AudioTagger], which needs no
     * ffmpeg and never touches the audio frames. Only formats it cannot write
     * (WAV) go through an ffmpeg remux, and even then a failed remux keeps the
     * downloaded file.
     */
    private suspend fun finalizeAudio(
        meta: AudioMeta,
        workDir: File,
        produced: File,
        progress: ProgressSink,
    ): File {
        val format = produced.extension.lowercase(Locale.US)
        if (format !in TAGGED_CONTAINERS) return produced
        progress.report(95f, 0L, "Fetching cover art…")

        val cover = if (format in COVER_CONTAINERS) {
            CoverArt.fetchFor(context, meta, workDir, "tagged")
        } else {
            null
        }
        try {
            if (AudioTagger.supports(format)) {
                progress.report(97f, 0L, "Adding tags and cover art…")
                val written = AudioTagger.embed(produced, meta, cover)
                Log.i(
                    TAG,
                    "Tagged ${produced.name}: written=$written " +
                        "cover=${if (cover != null) "${cover.length()} bytes" else "none"}",
                )
                if (written) return produced
            }

            // WAV, or a file the writer could not parse: let ffmpeg rebuild the
            // tag block, and keep the downloaded original if even that fails.
            progress.report(96f, 0L, "Adding tags and cover art…")
            val tagged = File(workDir, "tagged.${produced.extension}")
            return try {
                var embedded = runCatching {
                    FfmpegRunner.convertAudio(
                        context = context,
                        source = produced,
                        output = tagged,
                        format = format,
                        codec = null,
                        copyAudio = true,
                        tags = meta.tags(),
                        cover = cover,
                    )
                }.isSuccess
                if (!embedded && cover != null) {
                    currentCoroutineContext().ensureActive()
                    tagged.delete()
                    embedded = runCatching {
                        FfmpegRunner.convertAudio(
                            context = context,
                            source = produced,
                            output = tagged,
                            format = format,
                            codec = null,
                            copyAudio = true,
                            tags = meta.tags(),
                            cover = null,
                        )
                    }.isSuccess
                }
                if (embedded && tagged.isFile && tagged.length() > 0L) {
                    AudioTagger.embed(tagged, meta, cover)
                    produced.delete()
                    tagged
                } else {
                    tagged.delete()
                    produced
                }
            } catch (t: Throwable) {
                tagged.delete()
                throw t
            }
        } finally {
            cover?.delete()
        }
    }

    /** True for containers our tag writer (or the ffmpeg fallback) handles. */
    private fun isAudioFile(file: File): Boolean =
        file.extension.lowercase(Locale.US) in TAGGED_CONTAINERS

    /** Newest complete media file in a work directory. */
    private fun newestMediaFile(workDir: File): File? =
        workDir.listFiles()
            ?.filter { file ->
                file.isFile &&
                    file.length() > 0L &&
                    !file.name.endsWith(".part") &&
                    !file.name.endsWith(".ytdl") &&
                    file.extension.lowercase(Locale.US) in MEDIA_EXTENSIONS
            }
            ?.maxByOrNull { it.lastModified() }

    private companion object {
        const val TAG = "ReelRelayDownload"
        val MEDIA_EXTENSIONS = setOf(
            "mp4", "m4v", "webm", "mkv", "mov",
            "m4a", "mp3", "flac", "wav", "opus", "ogg",
        )
    }
}

/**
 * Turns many progress callbacks into one monotonic value.
 *
 * A failed parallel attempt is followed by a fresh yt-dlp run that starts at
 * 0%. [beginFallback] remembers where the bar stopped and maps the fallback's
 * 0–100% onto the remaining range, so the bar continues forwards instead of
 * restarting or freezing.
 */
internal class ProgressSink(
    private val emit: (Float, Long, String) -> Unit,
) {
    private var last = 0f
    private var fallback = false
    private var fallbackBase = 0f

    val progress: Float get() = last

    fun report(value: Float, eta: Long = 0L, line: String = "") {
        val raw = value.coerceIn(0f, 100f)
        val mapped = if (fallback) {
            fallbackBase + (99f - fallbackBase) * raw / 100f
        } else {
            raw
        }
        val shown = maxOf(last, mapped).coerceAtMost(99f)
        val advanced = shown > last
        last = shown
        if (advanced || line.isNotBlank()) emit(shown, eta, line)
    }

    fun beginFallback() {
        fallback = true
        fallbackBase = (last - 4f).coerceIn(0f, 90f)
    }
}

/** Copies a finished file into Downloads/ReelRelay so it survives app removal. */
object MediaPublisher {

    private const val TAG = "ReelRelayPublish"

    fun publish(context: Context, file: File): Uri? {
        val mime = mimeType(file)
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            publishModern(context, file, mime)
        } else {
            publishLegacy(context, file, mime)
        }
    }

    private fun mimeType(file: File): String = when (file.extension.lowercase()) {
        "mp4", "m4v" -> "video/mp4"
        "webm" -> "video/webm"
        "mkv" -> "video/x-matroska"
        "mov" -> "video/quicktime"
        "m4a" -> "audio/mp4"
        "mp3" -> "audio/mpeg"
        "flac" -> "audio/flac"
        "wav" -> "audio/wav"
        "opus", "ogg" -> "audio/ogg"
        else -> "application/octet-stream"
    }

    @RequiresApi(Build.VERSION_CODES.Q)
    private fun publishModern(context: Context, file: File, mime: String): Uri? {
        val resolver = context.contentResolver
        val collection = MediaStore.Downloads.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY)
        val values = ContentValues().apply {
            put(MediaStore.MediaColumns.DISPLAY_NAME, file.name)
            put(MediaStore.MediaColumns.MIME_TYPE, mime)
            put(
                MediaStore.MediaColumns.RELATIVE_PATH,
                "${Environment.DIRECTORY_DOWNLOADS}/ReelRelay",
            )
            put(MediaStore.MediaColumns.IS_PENDING, 1)
        }
        val uri = resolver.insert(collection, values) ?: return null
        return try {
            val output = resolver.openOutputStream(uri)
                ?: throw IllegalStateException("Could not open the Downloads collection.")
            output.use { stream ->
                file.inputStream().use { input -> input.copyTo(stream) }
            }
            val done = ContentValues().apply {
                put(MediaStore.MediaColumns.IS_PENDING, 0)
            }
            resolver.update(uri, done, null, null)
            Log.i(TAG, "Saved ${locationOf(context, uri)} (${file.length()} bytes)")
            uri
        } catch (t: Throwable) {
            resolver.delete(uri, null, null)
            throw t
        }
    }

    /**
     * Human-readable path of a published file, as MediaStore actually stored
     * it. Some OEMs ignore `RELATIVE_PATH`, so the UI reports the real place
     * instead of assuming `Downloads/ReelRelay`.
     */
    fun locationOf(context: Context, uri: Uri): String? {
        if (uri.scheme != "content") return uri.path
        val projection = arrayOf(
            MediaStore.MediaColumns.RELATIVE_PATH,
            MediaStore.MediaColumns.DISPLAY_NAME,
        )
        return runCatching {
            context.contentResolver.query(uri, projection, null, null, null)?.use { cursor ->
                if (!cursor.moveToFirst()) return@use null
                val path = cursor.getString(0).orEmpty()
                val name = cursor.getString(1).orEmpty()
                when {
                    path.isBlank() && name.isBlank() -> null
                    path.isBlank() -> name
                    else -> path + name
                }
            }
        }.getOrNull()
    }

    @Suppress("DEPRECATION")
    private fun publishLegacy(context: Context, file: File, mime: String): Uri? {
        val downloads = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS)
        val targetDir = File(downloads, "ReelRelay")
        if (!targetDir.exists() && !targetDir.mkdirs()) return null
        val target = uniqueTarget(targetDir, file.name)
        file.copyTo(target, overwrite = false)
        MediaScannerConnection.scanFile(context, arrayOf(target.absolutePath), arrayOf(mime), null)
        return Uri.fromFile(target)
    }

    private fun uniqueTarget(directory: File, name: String): File {
        var target = File(directory, name)
        if (!target.exists()) return target
        val stem = name.substringBeforeLast('.', name)
        val suffix = name.substringAfterLast('.', "")
        var index = 1
        while (target.exists()) {
            val candidate = if (suffix.isEmpty()) "$stem ($index)" else "$stem ($index).$suffix"
            target = File(directory, candidate)
            index++
        }
        return target
    }
}
