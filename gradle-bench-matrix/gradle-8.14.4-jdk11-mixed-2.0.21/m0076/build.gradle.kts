plugins {
    kotlin("jvm")
}

kotlin {
    jvmToolchain(11)
}

dependencies {
    implementation(project(":m0075"))
    implementation(kotlin("stdlib"))
}
