# syntax=docker/dockerfile:1.7@sha256:a57df69d0ea827fb7266491f2813635de6f17269be881f696fbfdf2d83dda33e

### BASE IMAGE ###

ARG SANDBOX_PLATFORM
ARG UBUNTU_DIGEST
FROM --platform=${SANDBOX_PLATFORM} ubuntu@${UBUNTU_DIGEST} AS base

ARG DEBIAN_FRONTEND=noninteractive
ARG UBUNTU_SNAPSHOT_TIMESTAMP
ARG CA_BUNDLE_URL
ARG CA_BUNDLE_SHA256
ENV LANG=C.UTF-8

# Ubuntu's minimal image does not include a CA bundle. Bootstrap one through
# BuildKit with a content checksum, then install Ubuntu's managed bundle from
# the fixed official snapshot. Disable docker-clean so later cache mounts keep
# package archives outside the resulting image instead of serializing empty
# caches across the source-build stages.
ADD --checksum=${CA_BUNDLE_SHA256} \
    ${CA_BUNDLE_URL} /etc/ssl/certs/ca-certificates.crt

RUN chmod 0644 /etc/ssl/certs/ca-certificates.crt \
    && rm -f /etc/apt/apt.conf.d/docker-clean \
    && printf '%s\n' 'Binary::apt::APT::Keep-Downloaded-Packages "true";' > /etc/apt/apt.conf.d/keep-cache \
    && printf '%s\n' \
      'Types: deb' \
      "URIs: https://snapshot.ubuntu.com/ubuntu/${UBUNTU_SNAPSHOT_TIMESTAMP}/" \
      'Suites: noble noble-updates noble-backports noble-security' \
      'Components: main universe restricted multiverse' \
      'Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg' \
      > /etc/apt/sources.list.d/ubuntu.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl xz-utils unzip \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*

### SOURCE-BUILT RUNTIMES ###

# Keep expensive source builds in independent stages so BuildKit can reuse each
# runtime without carrying compilers, source trees, or archives into the image.
# Strip native debug information before COPY, so it never enters runtime layers.

### PYTHON ###

FROM base AS python_builder
ARG PYTHON_VERSION
ARG PYTHON_SHA256
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update \
    && apt-get install -y --no-install-recommends \
      build-essential libbz2-dev libffi-dev libgdbm-dev liblzma-dev \
      libncurses-dev libreadline-dev libsqlite3-dev libssl-dev tk-dev uuid-dev zlib1g-dev \
    && curl -fsSL --retry 5 --retry-all-errors --connect-timeout 15 \
      "https://www.python.org/ftp/python/${PYTHON_VERSION}/Python-${PYTHON_VERSION}.tar.xz" \
      -o /tmp/python.tar.xz \
    && echo "${PYTHON_SHA256}  /tmp/python.tar.xz" | sha256sum --check --strict \
    && mkdir -p /tmp/python-src \
    && tar -xJf /tmp/python.tar.xz -C /tmp/python-src --strip-components=1 \
    && cd /tmp/python-src \
    && ./configure \
      --prefix="/opt/python/${PYTHON_VERSION}" \
      --enable-optimizations \
      --enable-shared \
      --with-ensurepip=install \
      --with-lto \
      --without-static-libpython \
    && make -j"$(nproc)" \
    && make install \
    && find "/opt/python/${PYTHON_VERSION}" -type d -name __pycache__ -prune -exec rm -rf '{}' + \
    && find "/opt/python/${PYTHON_VERSION}" -type f -name '*.py[co]' -delete

# Keep slimming separate from compilation so existing compiler output is reusable.
RUN strip --strip-debug "/opt/python/${PYTHON_VERSION}/bin/python${PYTHON_VERSION%.*}" \
    && find "/opt/python/${PYTHON_VERSION}" -type f \( -name '*.so' -o -name '*.so.*' \) -exec strip --strip-debug '{}' +

### PHP ###

FROM base AS php_builder
ARG PHP_VERSION
ARG PHP_SHA256
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update \
    && apt-get install -y --no-install-recommends \
      autoconf bison build-essential pkg-config re2c \
      libcurl4-openssl-dev libffi-dev libicu-dev libonig-dev libpq-dev \
      libreadline-dev libsqlite3-dev libssl-dev libxml2-dev libxslt1-dev \
      libzip-dev zlib1g-dev \
    && curl -fsSL --retry 5 --retry-all-errors --connect-timeout 15 "https://www.php.net/distributions/php-${PHP_VERSION}.tar.xz" -o /tmp/php.tar.xz \
    && echo "${PHP_SHA256}  /tmp/php.tar.xz" | sha256sum --check --strict \
    && mkdir -p /tmp/php-src \
    && tar -xJf /tmp/php.tar.xz -C /tmp/php-src --strip-components=1 \
    && cd /tmp/php-src \
    && ./configure \
      --prefix="/opt/php/${PHP_VERSION}" \
      --with-config-file-path=/etc/php \
      --disable-cgi \
      --disable-phpdbg \
      --enable-bcmath \
      --enable-intl \
      --enable-mbstring \
      --enable-opcache \
      --enable-pcntl \
      --enable-sockets \
      --with-curl \
      --with-openssl \
      --with-pdo-mysql \
      --with-pdo-pgsql \
      --with-pdo-sqlite \
      --with-readline \
      --with-sqlite3 \
      --with-zip \
      --with-zlib \
    && make -j"$(nproc)" \
    && make install \
    && strip --strip-unneeded "/opt/php/${PHP_VERSION}/bin/php"

### RUBY ###

FROM base AS ruby_builder
ARG RUBY_VERSION
ARG RUBY_SERIES
ARG RUBY_SHA256
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update \
    && apt-get install -y --no-install-recommends \
      autoconf build-essential libffi-dev libgdbm-dev libreadline-dev \
      libssl-dev libyaml-dev zlib1g-dev \
    && curl -fsSL --retry 5 --retry-all-errors --connect-timeout 15 \
      "https://cache.ruby-lang.org/pub/ruby/${RUBY_SERIES}/ruby-${RUBY_VERSION}.tar.xz" \
      -o /tmp/ruby.tar.xz \
    && echo "${RUBY_SHA256}  /tmp/ruby.tar.xz" | sha256sum --check --strict \
    && mkdir -p /tmp/ruby-src \
    && tar -xJf /tmp/ruby.tar.xz -C /tmp/ruby-src --strip-components=1 \
    && cd /tmp/ruby-src \
    && ./configure --prefix="/opt/ruby/${RUBY_VERSION}" --disable-install-doc --enable-shared \
    && make -j"$(nproc)" \
    && make install \
    && find "/opt/ruby/${RUBY_VERSION}" -type f \( -name '*.a' -o -name '*.c' \) -delete

RUN strip --strip-debug "/opt/ruby/${RUBY_VERSION}/bin/ruby" \
    && find "/opt/ruby/${RUBY_VERSION}" -type f \( -name '*.so' -o -name '*.so.*' \) -exec strip --strip-debug '{}' +

### PREBUILT RUNTIME ASSETS ###

# Download each fixed runtime in its own stage and accept it only after its
# repository-pinned checksum matches.

### NODE.JS ###

FROM base AS node_asset
ARG NODE_VERSION
ARG NODE_ARCH
ARG NODE_SHA256
RUN curl -fsSL --retry 5 --retry-all-errors --connect-timeout 15 \
      "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-${NODE_ARCH}.tar.xz" \
      -o /tmp/node.tar.xz \
    && echo "${NODE_SHA256}  /tmp/node.tar.xz" | sha256sum --check --strict \
    && mkdir -p "/opt/node/${NODE_VERSION}" \
    && tar -xJf /tmp/node.tar.xz -C "/opt/node/${NODE_VERSION}" --strip-components=1 \
    && rm -rf "/opt/node/${NODE_VERSION}/share/doc"

### GO ###

FROM base AS go_asset
ARG GO_VERSION
ARG GO_ARCH
ARG GO_SHA256
RUN curl -fsSL --retry 5 --retry-all-errors --connect-timeout 15 \
      "https://go.dev/dl/go${GO_VERSION}.linux-${GO_ARCH}.tar.gz" -o /tmp/go.tar.gz \
    && echo "${GO_SHA256}  /tmp/go.tar.gz" | sha256sum --check --strict \
    && mkdir -p "/opt/go/${GO_VERSION}" \
    && tar -xzf /tmp/go.tar.gz -C "/opt/go/${GO_VERSION}" --strip-components=1 \
    && rm -rf "/opt/go/${GO_VERSION}/api" "/opt/go/${GO_VERSION}/doc" "/opt/go/${GO_VERSION}/test"

### JAVA ###

FROM base AS java_asset
ARG JAVA_VERSION
ARG JAVA_RELEASE_TAG
ARG JAVA_ARCHIVE
ARG JAVA_SHA256
RUN curl -fsSL --retry 5 --retry-all-errors --connect-timeout 15 \
      "https://github.com/adoptium/temurin25-binaries/releases/download/${JAVA_RELEASE_TAG}/${JAVA_ARCHIVE}" \
      -o /tmp/java.tar.gz \
    && echo "${JAVA_SHA256}  /tmp/java.tar.gz" | sha256sum --check --strict \
    && mkdir -p "/opt/java/${JAVA_VERSION}" \
    && tar -xzf /tmp/java.tar.gz -C "/opt/java/${JAVA_VERSION}" --strip-components=1 \
    && rm -rf "/opt/java/${JAVA_VERSION}/demo" "/opt/java/${JAVA_VERSION}/man"

### BUN ###

FROM base AS bun_asset
ARG BUN_VERSION
ARG BUN_ASSET
ARG BUN_SHA256
RUN curl -fsSL --retry 5 --retry-all-errors --connect-timeout 15 \
      "https://github.com/oven-sh/bun/releases/download/bun-v${BUN_VERSION}/${BUN_ASSET}.zip" \
      -o /tmp/bun.zip \
    && echo "${BUN_SHA256}  /tmp/bun.zip" | sha256sum --check --strict \
    && unzip -q /tmp/bun.zip -d /tmp/bun \
    && install -Dm0755 "/tmp/bun/${BUN_ASSET}/bun" /opt/bun/bin/bun \
    && ln -s bun /opt/bun/bin/bunx

### NODE PACKAGE MANAGERS ###

FROM base AS node_package_manager_assets
ARG YARN_VERSION
ARG YARN_TARBALL_URL
ARG YARN_SHA256
ARG PNPM_VERSION
ARG PNPM_TARBALL_URL
ARG PNPM_SHA256
RUN curl -fsSL --retry 5 --retry-all-errors --connect-timeout 15 \
      "${YARN_TARBALL_URL}" -o /tmp/yarn.tgz \
    && echo "${YARN_SHA256}  /tmp/yarn.tgz" | sha256sum --check --strict \
    && tar -xOf /tmp/yarn.tgz package/package.json | grep -Fq "\"version\": \"${YARN_VERSION}\"" \
    && mkdir -p /opt/node-package-managers/lib/node_modules/yarn \
    && tar -xzf /tmp/yarn.tgz -C /opt/node-package-managers/lib/node_modules/yarn --strip-components=1 \
    && curl -fsSL --retry 5 --retry-all-errors --connect-timeout 15 \
      "${PNPM_TARBALL_URL}" -o /tmp/pnpm.tgz \
    && echo "${PNPM_SHA256}  /tmp/pnpm.tgz" | sha256sum --check --strict \
    && tar -xOf /tmp/pnpm.tgz package/package.json | grep -Fq "\"version\": \"${PNPM_VERSION}\"" \
    && mkdir -p /opt/node-package-managers/lib/node_modules/pnpm /opt/node-package-managers/bin \
    && tar -xzf /tmp/pnpm.tgz -C /opt/node-package-managers/lib/node_modules/pnpm --strip-components=1 \
    && ln -s ../lib/node_modules/yarn/bin/yarn.js /opt/node-package-managers/bin/yarn \
    && ln -s ../lib/node_modules/yarn/bin/yarn.js /opt/node-package-managers/bin/yarnpkg \
    && ln -s ../lib/node_modules/pnpm/bin/pnpm.cjs /opt/node-package-managers/bin/pnpm \
    && ln -s ../lib/node_modules/pnpm/bin/pnpx.cjs /opt/node-package-managers/bin/pnpx

### RUST ###

FROM base AS rust_asset
ARG RUST_VERSION
ARG RUST_TARGET
ARG RUST_SHA256
RUN curl -fsSL --retry 5 --retry-all-errors --connect-timeout 15 \
      "https://static.rust-lang.org/dist/rust-${RUST_VERSION}-${RUST_TARGET}.tar.xz" \
      -o /tmp/rust.tar.xz \
    && echo "${RUST_SHA256}  /tmp/rust.tar.xz" | sha256sum --check --strict \
    && mkdir -p /tmp/rust \
    && tar -xJf /tmp/rust.tar.xz -C /tmp/rust --strip-components=1 \
    && /tmp/rust/install.sh \
      --prefix="/opt/rust/${RUST_VERSION}" \
      --disable-ldconfig \
      --components="rustc,rust-std-${RUST_TARGET},cargo,rustfmt-preview,clippy-preview,rust-analyzer-preview,llvm-tools-preview" \
    && rm -rf "/opt/rust/${RUST_VERSION}/share/doc"

# LLVM tools can ship a second copy of rustc's shared LLVM library. Reuse it only
# when both payloads are byte-identical; retain every tool and its expected path.
RUN for llvm_library in "/opt/rust/${RUST_VERSION}/lib/rustlib/${RUST_TARGET}/lib/"libLLVM.so*; do \
      [ -f "$llvm_library" ] || continue; \
      library_name="${llvm_library##*/}"; \
      shared_library="/opt/rust/${RUST_VERSION}/lib/$library_name"; \
      if [ -f "$shared_library" ] && cmp -s "$shared_library" "$llvm_library"; then \
        ln -sfn "../../../$library_name" "$llvm_library"; \
      fi; \
    done

### DEVELOPMENT TOOL ASSETS ###

# Collect uv, Maven, Gradle, Composer, and the Docker CLI for the
# final runtime stage without retaining their downloaded archives.

FROM base AS tool_assets
ARG UV_VERSION
ARG UV_TARGET
ARG UV_SHA256
ARG MAVEN_VERSION
ARG MAVEN_SHA512
ARG GRADLE_VERSION
ARG GRADLE_SHA256
ARG COMPOSER_VERSION
ARG COMPOSER_SHA256
ARG DOCKER_CLI_VERSION
ARG DOCKER_CLI_ARCH
ARG DOCKER_CLI_SHA256
RUN curl -fsSL --retry 5 --retry-all-errors --connect-timeout 15 \
      "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-${UV_TARGET}.tar.gz" \
      -o /tmp/uv.tar.gz \
    && echo "${UV_SHA256}  /tmp/uv.tar.gz" | sha256sum --check --strict \
    && tar -xzf /tmp/uv.tar.gz -C /tmp \
    && install -Dm0755 "/tmp/uv-${UV_TARGET}/uv" /opt/tools/bin/uv \
    && install -Dm0755 "/tmp/uv-${UV_TARGET}/uvx" /opt/tools/bin/uvx \
    && curl -fsSL --retry 5 --retry-all-errors --connect-timeout 15 \
      "https://archive.apache.org/dist/maven/maven-3/${MAVEN_VERSION}/binaries/apache-maven-${MAVEN_VERSION}-bin.tar.gz" \
      -o /tmp/maven.tar.gz \
    && echo "${MAVEN_SHA512}  /tmp/maven.tar.gz" | sha512sum --check --strict \
    && mkdir -p "/opt/maven/${MAVEN_VERSION}" \
    && tar -xzf /tmp/maven.tar.gz -C "/opt/maven/${MAVEN_VERSION}" --strip-components=1 \
    && rm -rf "/opt/maven/${MAVEN_VERSION}/lib/ext" \
    && curl -fsSL --retry 5 --retry-all-errors --connect-timeout 15 \
      "https://services.gradle.org/distributions/gradle-${GRADLE_VERSION}-bin.zip" \
      -o /tmp/gradle.zip \
    && echo "${GRADLE_SHA256}  /tmp/gradle.zip" | sha256sum --check --strict \
    && unzip -q /tmp/gradle.zip -d /tmp \
    && mkdir -p "/opt/gradle/${GRADLE_VERSION}" \
    && cp -a "/tmp/gradle-${GRADLE_VERSION}/." "/opt/gradle/${GRADLE_VERSION}/" \
    && curl -fsSL --retry 5 --retry-all-errors --connect-timeout 15 \
      "https://getcomposer.org/download/${COMPOSER_VERSION}/composer.phar" \
      -o /tmp/composer.phar \
    && echo "${COMPOSER_SHA256}  /tmp/composer.phar" | sha256sum --check --strict \
    && install -Dm0755 /tmp/composer.phar /opt/tools/bin/composer \
    && curl -fsSL --retry 5 --retry-all-errors --connect-timeout 15 \
      "https://download.docker.com/linux/static/stable/${DOCKER_CLI_ARCH}/docker-${DOCKER_CLI_VERSION}.tgz" \
      -o /tmp/docker.tgz \
    && echo "${DOCKER_CLI_SHA256}  /tmp/docker.tgz" | sha256sum --check --strict \
    && tar -xzf /tmp/docker.tgz -C /tmp \
    && install -Dm0755 /tmp/docker/docker /opt/tools/bin/docker

### CLAUDE CODE ###

FROM base AS claude_asset
ARG CLAUDE_VERSION
ARG CLAUDE_NPM_PACKAGE
ARG CLAUDE_SHA256
RUN curl -fsSL --retry 5 --retry-all-errors --connect-timeout 15 \
      "https://registry.npmjs.org/@anthropic-ai/${CLAUDE_NPM_PACKAGE}/-/${CLAUDE_NPM_PACKAGE}-${CLAUDE_VERSION}.tgz" \
      -o /tmp/claude.tgz \
    && echo "${CLAUDE_SHA256}  /tmp/claude.tgz" | sha256sum --check --strict \
    && mkdir -p /tmp/claude \
    && tar -xzf /tmp/claude.tgz -C /tmp/claude \
    && install -Dm0755 /tmp/claude/package/claude /opt/claude-code/bin/claude

# Claude Code restricts --sdk-url to Anthropic-owned hosts. OMA uses its own
# code-session endpoint, so patch the fixed upstream artifact in a disposable
# build stage. Python and the backup never enter the runtime image.
FROM base AS claude_patched_asset
ARG CLAUDE_PATCHED_SHA256
COPY --from=claude_asset /opt/claude-code/bin/claude /opt/claude-code/bin/claude
COPY image/patch-claude-sdk-url.py /tmp/patch-claude-sdk-url.py
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update \
    && apt-get install -y --no-install-recommends python3 \
    && python3 /tmp/patch-claude-sdk-url.py \
      /opt/claude-code/bin/claude \
      --backup /tmp/claude.sdk-url.bak \
    && echo "${CLAUDE_PATCHED_SHA256}  /opt/claude-code/bin/claude" | sha256sum --check --strict \
    && rm -f /tmp/claude.sdk-url.bak /tmp/patch-claude-sdk-url.py

### ENVIRONMENT MANAGER ###

# The independent environment-manager release is consumed from its immutable
# OCI image. versions.env pins one platform manifest per architecture; the final
# stage additionally verifies the copied binary bytes.
FROM --platform=${SANDBOX_PLATFORM} environment_manager AS environment_manager_source

### RCLONE FILESTORE ###

# rclone-filestore is independently built and consumed from an immutable OCI
# image context. The registry location is supplied by the builder; versions.env
# pins the accepted per-architecture manifest and payload bytes.
FROM --platform=${SANDBOX_PLATFORM} rclone_filestore AS rclone_filestore_source

### RUNTIME CONFIGURATION FILES ###

# Assemble tiny files independently, then copy them into the runtime in one
# layer. Keep large toolchains in separate layers so unchanged ones stay reusable.
FROM scratch AS runtime_config
COPY image/config/pip.conf /etc/pip.conf
COPY image/config/npmrc /etc/npmrc
COPY image/config/sudoers-user /etc/sudoers.d/user
COPY image/config/sudoers-claude /etc/sudoers.d/claude
COPY image/config/cargo-config.toml /home/user/.cargo/config.toml
COPY image/config/maven-settings.xml /home/user/.m2/settings.xml
COPY image/config/gradle-init.gradle /home/user/.gradle/init.gradle
COPY image/config/gemrc /home/user/.gemrc
COPY image/config/bundler-config /home/user/.bundle/config
COPY image/config/composer-config.json /home/user/.config/composer/config.json
COPY image/config/launcher-settings.json /root/.claude/launcher-settings.json
COPY image/verify-contract.sh /usr/local/bin/verify-sandbox-contract
COPY image/config/profile.sh /etc/profile.d/oma-sandbox.sh
COPY versions.env /etc/oma-sandbox-versions.env

### FINAL RUNTIME IMAGE ###

FROM base AS runtime

### RUNTIME CONTRACT AND ENVIRONMENT ###

ARG SANDBOX_PLATFORM
ARG UBUNTU_DIGEST
ARG APT_RUNTIME_MIRROR
ARG RUNTIME_REVISION
ARG CODEX_UNIVERSAL_RECIPE_REVISION
ARG ENVD_VERSION
ARG ENVD_ARCH
ARG PYTHON_VERSION
ARG NODE_VERSION
ARG GO_VERSION
ARG JAVA_VERSION
ARG PHP_VERSION
ARG GCC_VERSION
ARG GCC_MAJOR
ARG GCC_APT_VERSION
ARG BUN_VERSION
ARG RUST_VERSION
ARG RUST_TARGET
ARG RUBY_VERSION
ARG CLAUDE_VERSION
ARG MAVEN_VERSION
ARG GRADLE_VERSION
ARG ENVIRONMENT_MANAGER_IMAGE_DIGEST
ARG ENVIRONMENT_MANAGER_REVISION
ARG ENVIRONMENT_MANAGER_VERSION
ARG RCLONE_FILESTORE_IMAGE_DIGEST
ENV HOME=/home/user \
    VIRTUAL_ENV=/home/user/.local/share/oma/python \
    JAVA_HOME=/opt/java/current \
    CARGO_HOME=/home/user/.cargo \
    GOBIN=/home/user/go/bin \
    GOPROXY=https://goproxy.cn,direct \
    UV_DEFAULT_INDEX=https://mirrors.aliyun.com/pypi/simple/ \
    UV_NO_PROGRESS=1 \
    NPM_CONFIG_USERCONFIG=/etc/npmrc \
    BUN_CONFIG_REGISTRY=https://registry.npmmirror.com \
    NODE_PATH=/home/user/.local/lib/node_modules \
    GEM_HOME=/home/user/.local/share/gem \
    GEM_PATH=/home/user/.local/share/gem:/opt/ruby/current/lib/ruby/gems/current \
    GEMRC=/home/user/.gemrc \
    BUNDLE_USER_CONFIG=/home/user/.bundle/config \
    MAVEN_HOME=/opt/maven/current \
    MAVEN_ARGS="-s /home/user/.m2/settings.xml" \
    GRADLE_HOME=/opt/gradle/current \
    GRADLE_USER_HOME=/home/user/.gradle \
    COMPOSER_HOME=/home/user/.config/composer \
    PATH=/home/user/.local/share/oma/python/bin:/home/user/.local/bin:/home/user/go/bin:/home/user/.cargo/bin:/home/user/.local/share/gem/bin:/opt/python/current/bin:/opt/node/current/bin:/opt/go/current/bin:/opt/java/current/bin:/opt/php/current/bin:/opt/bun/bin:/opt/rust/current/bin:/opt/ruby/current/bin:/opt/maven/current/bin:/opt/gradle/current/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

### SYSTEM PACKAGES AND FIXED COMPILER ###

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update \
    && apt-get install -y --no-install-recommends \
      gcc-${GCC_MAJOR}=${GCC_APT_VERSION} g++-${GCC_MAJOR}=${GCC_APT_VERSION} \
      ca-certificates cmake curl default-libmysqlclient-dev diffutils gawk git htop jq \
      libc6-dev libbz2-dev libcurl4-openssl-dev libffi-dev libgdbm-dev libicu-dev \
      liblzma-dev libncurses-dev libonig-dev libpq-dev libreadline-dev libsqlite3-dev \
      libssl-dev libxml2-dev libxslt1-dev libyaml-dev libzip-dev make nano openssh-client \
      patch pkg-config postgresql-client redis-tools ripgrep screen sqlite3 sudo tar tmux \
      tree unzip vim wget zip zlib1g-dev \
    && ln -sf "/usr/bin/gcc-${GCC_MAJOR}" /usr/local/bin/gcc \
    && ln -sf "/usr/bin/gcc-${GCC_MAJOR}" /usr/local/bin/cc \
    && ln -sf "/usr/bin/g++-${GCC_MAJOR}" /usr/local/bin/g++ \
    && ln -sf "/usr/bin/g++-${GCC_MAJOR}" /usr/local/bin/c++ \
    && printf '%s\n' \
      'Types: deb' \
      "URIs: ${APT_RUNTIME_MIRROR}" \
      'Suites: noble noble-updates noble-backports noble-security' \
      'Components: main universe restricted multiverse' \
      'Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg' \
      > /etc/apt/sources.list.d/ubuntu.sources

### RUNTIME AND TOOL ARTIFACTS ###

COPY --from=python_builder /opt/python/ /opt/python/
COPY --from=php_builder /opt/php/ /opt/php/
COPY --from=ruby_builder /opt/ruby/ /opt/ruby/
COPY --from=node_asset /opt/node/ /opt/node/
COPY --from=go_asset /opt/go/ /opt/go/
COPY --from=java_asset /opt/java/ /opt/java/
COPY --from=bun_asset /opt/bun/ /opt/bun/
COPY --from=node_package_manager_assets --chown=1001:1001 /opt/node-package-managers/ /home/claude/.npm-global/
COPY --from=rust_asset /opt/rust/ /opt/rust/
COPY --from=tool_assets /opt/tools/bin/ /usr/local/bin/
COPY --from=tool_assets /opt/maven/ /opt/maven/
COPY --from=tool_assets /opt/gradle/ /opt/gradle/
COPY --from=claude_patched_asset /opt/claude-code/ /opt/claude-code/
COPY --from=environment_manager_source /usr/local/bin/environment-manager /opt/env-runner/environment-manager
COPY --from=rclone_filestore_source /opt/rclone/rclone-filestore /opt/rclone/rclone-filestore
COPY image/envd-bin/envd-v${ENVD_VERSION}-linux-${ENVD_ARCH} /usr/bin/envd

### PACKAGE MANAGER, SUDO, AND SHELL CONFIGURATION ###

COPY --from=runtime_config / /

### RUNTIME LINKS, USERS, AND SHARED DIRECTORIES ###

ARG ENVIRONMENT_MANAGER_SHA256
ARG RCLONE_FILESTORE_SHA256
ARG ENVD_SHA256
ARG CLAUDE_PATCHED_SHA256
RUN echo "${ENVD_SHA256}  /usr/bin/envd" | sha256sum --check --strict \
    && echo "${ENVIRONMENT_MANAGER_SHA256}  /opt/env-runner/environment-manager" | sha256sum --check --strict \
    && echo "${RCLONE_FILESTORE_SHA256}  /opt/rclone/rclone-filestore" | sha256sum --check --strict \
    && echo "${CLAUDE_PATCHED_SHA256}  /opt/claude-code/bin/claude" | sha256sum --check --strict \
    && chmod 0440 /etc/sudoers.d/user /etc/sudoers.d/claude \
    && chmod 0644 /etc/profile.d/oma-sandbox.sh \
    && chmod 0755 /usr/bin/envd /opt/env-runner/environment-manager /opt/rclone/rclone-filestore /usr/local/bin/verify-sandbox-contract \
    && ln -s /opt/env-runner/environment-manager /usr/local/bin/environment-manager \
    && ln -s /opt/claude-code/bin/claude /usr/local/bin/claude \
    && ln -s "${PYTHON_VERSION}" /opt/python/current \
    && ln -s "${NODE_VERSION}" /opt/node/current \
    && ln -s "${GO_VERSION}" /opt/go/current \
    && ln -s "${JAVA_VERSION}" /opt/java/current \
    && ln -s "${PHP_VERSION}" /opt/php/current \
    && ln -s "${RUST_VERSION}" /opt/rust/current \
    && ln -s "${RUBY_VERSION}" /opt/ruby/current \
    && ln -s "${MAVEN_VERSION}" /opt/maven/current \
    && ln -s "${GRADLE_VERSION}" /opt/gradle/current \
    && printf '%s\n' /opt/python/current/lib /opt/ruby/current/lib > /etc/ld.so.conf.d/oma-runtimes.conf \
    && ldconfig \
    && ruby_api_version="$(/opt/ruby/current/bin/ruby -rrbconfig -e 'print RbConfig::CONFIG["ruby_version"]')" \
    && ln -s "$ruby_api_version" /opt/ruby/current/lib/ruby/gems/current \
    && cp -a /home/ubuntu/. /home/user/ \
    && usermod --login user --home /home/user --shell /bin/bash ubuntu \
    && groupmod --new-name user ubuntu \
    && rm -rf /home/ubuntu \
    && groupadd --gid 1001 claude \
    && useradd --uid 1001 --gid claude --create-home --shell /bin/bash claude \
    && usermod --append --groups claude user \
    && usermod --append --groups user claude \
    && mkdir -p \
      /root/.claude \
      /home/claude/.cache/pip \
      /home/claude/.claude \
      /home/claude/.local/bin \
      /home/claude/.npm \
      /home/claude/.npm-global/bin \
      /home/claude/.npm-global/lib/node_modules \
      /home/claude/project \
      /home/user/.local/bin \
      /home/user/.local/lib/node_modules \
      /home/user/.local/share/gem \
      /home/user/.local/share/oma \
      /home/user/go/bin \
    && printf '{}\n' > /root/.claude.json \
    && chmod 0700 /root/.claude \
    && chmod 0600 /root/.claude.json \
    && find /home/claude \( ! -user claude -o ! -group claude \) -exec chown -h claude:claude '{}' + \
    && chown -R user:user /home/user \
    && chmod 2770 /home/claude /home/user \
    && chmod 2775 \
      /home/claude/.cache \
      /home/claude/.cache/pip \
      /home/claude/.claude \
      /home/claude/.local \
      /home/claude/.local/bin \
      /home/claude/.npm \
      /home/claude/.npm-global \
      /home/claude/.npm-global/bin \
      /home/claude/.npm-global/lib \
      /home/claude/.npm-global/lib/node_modules \
      /home/claude/project \
    && printf '%s\n' \
      'GOPROXY=https://goproxy.cn,direct' \
      'UV_DEFAULT_INDEX=https://mirrors.aliyun.com/pypi/simple/' \
      'NPM_CONFIG_USERCONFIG=/etc/npmrc' \
      'BUN_CONFIG_REGISTRY=https://registry.npmmirror.com' \
      'PIP_CACHE_DIR=/home/claude/.cache/pip' \
      'PIP_CONFIG_FILE=/etc/pip.conf' \
      'IS_SANDBOX=yes' \
      'NODE_PATH=/home/claude/.npm-global/lib/node_modules' \
      'GEMRC=/home/user/.gemrc' \
      'BUNDLE_USER_CONFIG=/home/user/.bundle/config' \
      'MAVEN_ARGS=-s /home/user/.m2/settings.xml' \
      'GRADLE_USER_HOME=/home/user/.gradle' \
      'COMPOSER_HOME=/home/user/.config/composer' \
      >> /etc/environment

### CLAUDE-COMPATIBLE PROCESS ENVIRONMENT ###

ENV PIP_ROOT_USER_ACTION=ignore \
    PIP_CACHE_DIR=/home/claude/.cache/pip \
    PIP_CONFIG_FILE=/etc/pip.conf \
    PYTHONUNBUFFERED=1 \
    IS_SANDBOX=yes \
    NODE_PATH=/home/claude/.npm-global/lib/node_modules \
    PATH=/home/claude/.npm-global/bin:/home/claude/.local/bin:/opt/ruby/current/bin:${PATH}

### USER-SCOPED TOOL INITIALIZATION ###

USER user
WORKDIR /home/user

RUN "/opt/python/${PYTHON_VERSION}/bin/python3" -m venv "$VIRTUAL_ENV" \
    && rm -rf \
      /home/user/.cache \
      /home/claude/.npm/_cacache \
      /home/claude/.npm/_logs \
      /home/user/.local/share/pnpm/store \
      /home/user/.gem/specs \
      /home/user/.gradle/caches \
      /home/user/.m2/repository

### FINAL IDENTITY AND IMAGE METADATA ###

USER root
ENV HOME=/root

LABEL org.opencontainers.image.title="Open Managed Agents Sandbox Base" \
      org.opencontainers.image.description="Fixed-runtime ${SANDBOX_PLATFORM} base for E2B managed-agent sandboxes" \
      org.opencontainers.image.base.digest="${UBUNTU_DIGEST}" \
      ai.superduck.oma.sandbox.platform="${SANDBOX_PLATFORM}" \
      ai.superduck.oma.sandbox.runtime-revision="${RUNTIME_REVISION}" \
      ai.superduck.oma.sandbox.recipe-revision="${CODEX_UNIVERSAL_RECIPE_REVISION}" \
      ai.superduck.oma.sandbox.envd-version="${ENVD_VERSION}" \
      ai.superduck.oma.sandbox.claude-version="${CLAUDE_VERSION}" \
      ai.superduck.oma.sandbox.environment-manager-image-digest="${ENVIRONMENT_MANAGER_IMAGE_DIGEST}" \
      ai.superduck.oma.sandbox.environment-manager-revision="${ENVIRONMENT_MANAGER_REVISION}" \
      ai.superduck.oma.sandbox.environment-manager-version="${ENVIRONMENT_MANAGER_VERSION}" \
      ai.superduck.oma.sandbox.rclone-filestore-image-digest="${RCLONE_FILESTORE_IMAGE_DIGEST}"

EXPOSE 49983
ENTRYPOINT ["/usr/bin/envd", "-isnotfc", "-port", "49983"]

FROM runtime AS toolkit
ARG UBUNTU_SNAPSHOT_TIMESTAMP
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/oma-tools-browsers \
    OMA_LOCAL_ASR_MODEL=/opt/oma-models/whisper-small
COPY image/resources /opt/oma-resources
COPY image/mermaid /opt/mermaid
COPY image/resources/profile.sh /etc/profile.d/oma-toolkit.sh
RUN --mount=type=cache,target=/var/cache/oma-resources,sharing=locked \
    --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    python3 -m pip install --no-cache-dir --require-hashes --only-binary=:all: -r /opt/oma-resources/requirements.lock \
    && python3 -m pip check \
    && cp /etc/apt/sources.list.d/ubuntu.sources /tmp/oma-resources-ubuntu.sources \
    && sed -i "s|^URIs:.*|URIs: https://snapshot.ubuntu.com/ubuntu/${UBUNTU_SNAPSHOT_TIMESTAMP}/|" /etc/apt/sources.list.d/ubuntu.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
      libreoffice-writer libreoffice-calc libreoffice-impress poppler-utils poppler-data qpdf pandoc \
      fontconfig fonts-noto-cjk fonts-dejavu-core tesseract-ocr tesseract-ocr-chi-sim \
      tesseract-ocr-eng ffmpeg sox imagemagick espeak-ng graphviz plantuml \
    && python3 -m playwright install-deps chromium \
    && apt-get install -y --no-install-recommends libgomp1 \
    && mv /tmp/oma-resources-ubuntu.sources /etc/apt/sources.list.d/ubuntu.sources \
    && PUPPETEER_SKIP_DOWNLOAD=true npm ci --prefix /opt/mermaid --omit=dev --ignore-scripts --no-audit --no-fund \
    && ln -s /opt/mermaid/node_modules/.bin/mmdc /usr/local/bin/mmdc \
    && python3 /opt/oma-resources/install.py \
    && chmod -R a+rX /opt/oma-tools-browsers /opt/oma-models /opt/oma-resources /opt/mermaid
