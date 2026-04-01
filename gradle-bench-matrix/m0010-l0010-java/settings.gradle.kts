@file:Suppress("UnstableApiUsage")

import java.io.File
import java.time.Instant

dependencyResolutionManagement {
    repositories {
        mavenCentral()
    }
}

plugins {
    id("com.gradle.develocity") version "4.4.0"
}

rootProject.name = "gradle-bench-generated"

include("m0000")
include("m0001")
include("m0002")
include("m0003")
include("m0004")
include("m0005")
include("m0006")
include("m0007")
include("m0008")
include("m0009")

develocity {
    buildScan {
        uploadInBackground.set(false)
        termsOfUseUrl.set("https://gradle.com/help/legal-terms-of-use")
        termsOfUseAgree.set("yes")
    }
}
