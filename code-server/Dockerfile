FROM codercom/code-server:latest

USER root

ARG DOCKER_CLI_VERSION=28.2.2

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        git \
        openssh-client \
        sudo \
        tar \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL "https://download.docker.com/linux/static/stable/x86_64/docker-${DOCKER_CLI_VERSION}.tgz" \
        | tar -xz --strip-components=1 -C /usr/local/bin docker/docker \
    && echo "coder ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/coder \
    && chmod 0440 /etc/sudoers.d/coder

USER coder

RUN code-server --install-extension ms-azuretools.vscode-docker \
    && code-server --install-extension ms-vscode-remote.remote-containers || true

WORKDIR /home/coder/project

CMD ["--bind-addr", "0.0.0.0:8080", "/home/coder/project"]
