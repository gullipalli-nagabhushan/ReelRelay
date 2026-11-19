package com.reelrelay.app

import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.nio.file.Files

class FfmpegRunnerTest {

    @Test
    fun `loader path includes the unpacked ffmpeg libraries`() {
        val root = Files.createTempDirectory("rr-ffmpeg").toFile()
        try {
            val nativeDir = File(root, "lib-arm64").apply { mkdirs() }
            val noBackup = File(root, "noBackup").apply { mkdirs() }
            File(noBackup, FFMPEG_LIB_DIR).mkdirs()

            val path = ffmpegLibraryPath(nativeDir.absolutePath, noBackup)
                .replace('\\', '/')
            val native = nativeDir.absolutePath.replace('\\', '/')

            assertTrue(path.startsWith("$native:"))
            assertTrue(path.endsWith("/youtubedl-android/packages/ffmpeg/usr/lib"))
        } finally {
            root.deleteRecursively()
        }
    }

    @Test
    fun `a missing extraction is reported instead of silently failing`() {
        val root = Files.createTempDirectory("rr-ffmpeg").toFile()
        try {
            assertThrows(IllegalStateException::class.java) {
                ffmpegLibraryPath(root.absolutePath, root)
            }
        } finally {
            root.deleteRecursively()
        }
    }
}
