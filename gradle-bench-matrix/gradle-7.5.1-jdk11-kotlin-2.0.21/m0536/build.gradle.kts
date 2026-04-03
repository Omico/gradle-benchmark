plugins {
    kotlin("jvm")
}

kotlin {
    jvmToolchain(11)
}

dependencies {
    implementation(project(":m0535"))
    implementation(kotlin("stdlib"))
}
