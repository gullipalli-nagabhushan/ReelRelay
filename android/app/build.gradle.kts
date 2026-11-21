import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// Python and ffmpeg are bundled per ABI, so shipping all four costs ~200 MB.
// One ABI keeps the APK around 60 MB and covers every modern phone. Override:
//   ./gradlew assembleDebug -Preelrelay.abis=arm64-v8a,armeabi-v7a
val reelrelayAbis = (project.findProperty("reelrelay.abis") as String?)
    ?.split(",")
    ?.map { it.trim() }
    ?.filter { it.isNotEmpty() }
    ?: listOf("arm64-v8a")
val knownAbis = setOf("arm64-v8a", "armeabi-v7a", "x86", "x86_64")
require(reelrelayAbis.all { it in knownAbis }) {
    "Unknown ABI in -Preelrelay.abis: $reelrelayAbis (expected one of $knownAbis)"
}

android {
    namespace = "com.reelrelay.app"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.reelrelay.app"
        minSdk = 24
        targetSdk = 36
        versionCode = 4
        versionName = "3.1.1"

        ndk {
            abiFilters += reelrelayAbis
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    buildFeatures {
        viewBinding = true
        buildConfig = true
    }

    packaging {
        jniLibs {
            useLegacyPackaging = true
        }
        resources {
            excludes += "/META-INF/{AL2.0,LGPL2.1}"
        }
    }
}

kotlin {
    compilerOptions {
        jvmTarget.set(JvmTarget.JVM_17)
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("com.google.android.material:material:1.12.0")
    implementation("androidx.activity:activity-ktx:1.9.3")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.7")
    implementation("androidx.lifecycle:lifecycle-viewmodel-ktx:2.8.7")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.9.0")
    implementation("io.coil-kt:coil:2.7.0")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")

    implementation("androidx.media3:media3-exoplayer:1.5.1")
    implementation("androidx.media3:media3-ui:1.5.1")

    implementation("io.github.junkfood02.youtubedl-android:library:0.18.1")
    implementation("io.github.junkfood02.youtubedl-android:ffmpeg:0.18.1")

    // Already present at runtime through youtubedl-android; declaring it makes
    // the playlist JSON parser compile against the same classes.
    implementation("com.fasterxml.jackson.core:jackson-databind:2.17.2")

    testImplementation("junit:junit:4.13.2")
    testImplementation("com.squareup.okhttp3:mockwebserver:4.12.0")
}
