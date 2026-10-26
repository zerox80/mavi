# syntax=docker/dockerfile:1
FROM eclipse-temurin:25-jdk-noble

ARG ANDROID_COMMAND_LINE_TOOLS=15859902
# Published at https://developer.android.com/studio#command-line-tools-only
ARG ANDROID_COMMAND_LINE_TOOLS_SHA256=4e4c464f145a7512b57d088ac6c278c03c9eea610886b35a5e0804e74eedf583
ARG ANDROID_PLATFORM=android-37.1
ARG ANDROID_BUILD_TOOLS=37.0.0
ARG ANDROID_NDK_VERSION=28.1.13356709
ARG RUST_TOOLCHAIN=stable

ENV ANDROID_HOME=/opt/android-sdk \
    ANDROID_SDK_ROOT=/opt/android-sdk \
    CARGO_HOME=/opt/cargo \
    RUSTUP_HOME=/opt/rustup \
    GRADLE_USER_HOME=/cache/gradle \
    CARGO_TARGET_DIR=/cache/target \
    CARGO_NET_GIT_FETCH_WITH_CLI=true
ENV ANDROID_NDK_HOME=${ANDROID_HOME}/ndk/${ANDROID_NDK_VERSION} \
    ANDROID_NDK_ROOT=${ANDROID_HOME}/ndk/${ANDROID_NDK_VERSION} \
    PATH=/opt/cargo/bin:/opt/android-sdk/cmdline-tools/latest/bin:/opt/android-sdk/platform-tools:${PATH}

SHELL ["/bin/bash", "-o", "pipefail", "-c"]
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential ca-certificates cmake curl git pkg-config python3 unzip \
    && rm -rf /var/lib/apt/lists/*

RUN curl --fail --location --retry 3 \
        "https://dl.google.com/android/repository/commandlinetools-linux-${ANDROID_COMMAND_LINE_TOOLS}_latest.zip" \
        -o /tmp/android-tools.zip \
    && echo "${ANDROID_COMMAND_LINE_TOOLS_SHA256}  /tmp/android-tools.zip" | sha256sum --check - \
    && mkdir -p "${ANDROID_HOME}/cmdline-tools" \
    && unzip -q /tmp/android-tools.zip -d "${ANDROID_HOME}/cmdline-tools" \
    && mv "${ANDROID_HOME}/cmdline-tools/cmdline-tools" "${ANDROID_HOME}/cmdline-tools/latest" \
    && rm /tmp/android-tools.zip
# Ignore SIGPIPE from yes, but preserve sdkmanager's exit code.
RUN set +o pipefail; yes | sdkmanager --licenses > /dev/null
RUN sdkmanager --channel=3 "platform-tools" "platforms;${ANDROID_PLATFORM}" \
        "build-tools;${ANDROID_BUILD_TOOLS}" "ndk;${ANDROID_NDK_VERSION}"

RUN curl --fail --location --retry 3 https://sh.rustup.rs -o /tmp/rustup.sh \
    && sh /tmp/rustup.sh -y --profile minimal --default-toolchain "${RUST_TOOLCHAIN}" \
    && rm /tmp/rustup.sh \
    && rustup target add aarch64-linux-android armv7-linux-androideabi \
        i686-linux-android x86_64-linux-android \
    && cargo install cargo-ndk --locked

# AGP also uses its default build-tools revision independently of compileSdk.
ARG ANDROID_GRADLE_BUILD_TOOLS=36.0.0
RUN sdkmanager "build-tools;${ANDROID_GRADLE_BUILD_TOOLS}"

COPY build.py /usr/local/bin/android-build.py
WORKDIR /workspace
ENTRYPOINT ["python3", "-u", "/usr/local/bin/android-build.py"]
CMD ["assembleDebug"]
