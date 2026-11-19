package com.reelrelay.app

import android.content.ContentValues
import android.content.Context
import android.media.MediaScannerConnection
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import androidx.annotation.RequiresApi
import com.yausername.youtubedl_android.YoutubeDL
import com.yausername.youtubedl_android.YoutubeDLRequest
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File

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
        choice: FormatChoice,
        processId: String,
        onProgress: (Float, Long, String) -> Unit,
    ): File = withContext(Dispatchers.IO) {
        val workDir = File(context.cacheDir, "reelrelay-$processId")
        if (!workDir.exists() && !workDir.mkdirs()) {
            throw IllegalStateException("Could not create a temporary download folder.")
        }
        try {
            val request = YoutubeDLRequest(url)
            request.addOption("--no-playlist")
            request.addOption("--no-mtime")
            request.addOption("--newline")
            request.addOption("--no-part")
            request.addOption("-o", File(workDir, "%(title).150B.%(ext)s").absolutePath)
            request.addOption("-f", choice.formatSelector)

            if (choice.mergeToMp4) {
                request.addOption("--merge-output-format", "mp4")
            }
            choice.extractAudioFormat?.let { format ->
                request.addOption("--extract-audio")
                request.addOption("--audio-format", format)
                request.addOption("--audio-quality", "0")
            }

            YoutubeDL.getInstance().execute(request, processId) { progress, eta, line ->
                onProgress(progress, eta, line ?: "")
            }

            workDir.listFiles()
                ?.filter {
                    it.isFile &&
                        !it.name.endsWith(".part") &&
                        !it.name.endsWith(".ytdl")
                }
                ?.maxByOrNull { it.lastModified() }
                ?: throw IllegalStateException("The download finished but no file was produced.")
        } catch (t: Throwable) {
            workDir.deleteRecursively()
            throw t
        }
    }
}

/** Copies a finished file into Downloads/ReelRelay so it survives app removal. */
object MediaPublisher {

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
            uri
        } catch (t: Throwable) {
            resolver.delete(uri, null, null)
            throw t
        }
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
