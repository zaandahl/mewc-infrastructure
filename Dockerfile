# Controller only. Scientific workloads run on the configured remote host.
FROM python:3.12-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254

ARG TARGETARCH=amd64
ENV TERRAFORM_VERSION=1.16.2 \
    TERRAFORM_SHA256=0d17011f0c4664539b164b044903d04e296c86c13cb9f28040076c65cfb3985a \
    ANSIBLE_COLLECTIONS_PATH=/opt/ansible/collections \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN test "$TARGETARCH" = amd64 \
    && apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl unzip openssh-client rsync \
    && rm -rf /var/lib/apt/lists/* \
    && curl --fail --show-error --location --retry 3 \
       "https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_linux_amd64.zip" \
       --output /tmp/terraform.zip \
    && echo "${TERRAFORM_SHA256}  /tmp/terraform.zip" | sha256sum --check --strict \
    && unzip /tmp/terraform.zip terraform -d /usr/local/bin \
    && rm /tmp/terraform.zip

COPY requirements-controller.txt /opt/mewc/requirements-controller.txt
RUN pip install --no-cache-dir --require-hashes -r /opt/mewc/requirements-controller.txt
COPY ansible-requirements.yml /opt/mewc/ansible-requirements.yml
RUN ansible-galaxy collection install -r /opt/mewc/ansible-requirements.yml \
    && terraform version && ansible --version && openstack --version

WORKDIR /app
CMD ["bash"]
