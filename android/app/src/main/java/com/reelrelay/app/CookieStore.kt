package com.reelrelay.app

import android.content.Context
import android.net.Uri
import android.provider.OpenableColumns
import java.io.File

/**
 * Keeps an imported `cookies.txt` inside the app's private storage.
 *
 * Instagram and Facebook serve many Reels/Videos only to a logged-in session,
 * so yt-dlp has to be handed the browser cookie jar the desktop app accepts.
 * The file never leaves the app sandbox (no backup, no external permission).
 */
object CookieStore {

    private const val FILE_NAME = "cookies.txt"

    fun file(context: Context): File = File(context.filesDir, FILE_NAME)

    fun hasCookies(context: Context): Boolean {
        val file = file(context)
        return file.isFile && file.length() > 0L
    }

    /**
     * Copies the document the user picked into private storage and returns the
     * number of cookie lines, or `null` when the picks is not a usable jar.
     */
    fun import(context: Context, uri: Uri): Int? {
        val target = file(context)
        val temporary = File(context.cacheDir, "$FILE_NAME.import")
        val copied = context.contentResolver.openInputStream(uri)?.use { input ->
            temporary.outputStream().use { output -> input.copyTo(output) }
        } ?: return null
        if (copied <= 0L) {
            temporary.delete()
            return null
        }
        val lines = temporary.bufferedReader().useLines { sequence ->
            sequence.count { line -> line.isNotBlank() && !line.startsWith("#") }
        }
        if (lines == 0) {
            temporary.delete()
            return null
        }
        temporary.copyTo(target, overwrite = true)
        temporary.delete()
        return lines
    }

    fun clear(context: Context) {
        file(context).delete()
    }

    fun displayName(context: Context, uri: Uri): String {
        val name = context.contentResolver.query(uri, null, null, null, null)?.use { cursor ->
            val index = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
            if (index >= 0 && cursor.moveToFirst()) cursor.getString(index) else null
        }
        return name ?: "cookies.txt"
    }
}
