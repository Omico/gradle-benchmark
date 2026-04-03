plugins {
    kotlin("jvm")
}

kotlin {
    jvmToolchain(11)
}

dependencies {
    implementation(project(":m0104"))
    implementation(kotlin("stdlib"))
}
