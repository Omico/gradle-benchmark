plugins {
    kotlin("jvm")
}

kotlin {
    jvmToolchain(11)
}

dependencies {
    implementation(project(":m0689"))
    implementation(kotlin("stdlib"))
}
