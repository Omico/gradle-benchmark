plugins {
    kotlin("jvm")
}

kotlin {
    jvmToolchain(11)
}

dependencies {
    implementation(project(":m0563"))
    implementation(kotlin("stdlib"))
}
