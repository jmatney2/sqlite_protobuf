# BUILD (from repo root):
#   docker build -f grafana_demo/Dockerfile . -t grafana-demo
#
# RUN:
#   docker run --rm -p 3000:3000 -v ./demo/db.sqlite3:/data/db.sqlite3 --env GF_LOG_LEVEL=debug --name grafana-demo grafana-demo
#
# Credentials: admin / admin

# ── Stage 1: build the custom plugin backend ─────────────────────────────────
# The sqlite_protobuf .so is embedded into the binary via //go:embed so there
# is no runtime dependency on finding it in the filesystem.
FROM golang:1.26.1-trixie AS plugin-builder

RUN apt-get update && apt-get install -y gcc

WORKDIR /plugin
COPY grafana_demo/grafana-sqlite-datasource/ .

# Embed the pre-built sqlite_protobuf extension into the plugin source tree
# so that //go:embed can pick it up at compile time.
COPY django_sqlite_protobuf/django_sqlite_protobuf/libs/x86_64-unknown-linux-gnu/libsqlite_protobuf.so \
     pkg/plugin/libsqlite_protobuf.so

# CGO_ENABLED=1 is required for mattn/go-sqlite3.
RUN go mod tidy && \
    CGO_ENABLED=1 GOOS=linux go build \
        -o gpx_sqlite-datasource_linux_amd64 \
        ./pkg

# ── Stage 2: final Grafana image ─────────────────────────────────────────────
FROM grafana/grafana:main-ubuntu

# Install the released plugin to get the signed frontend assets,
# then replace only the backend binary with our custom build.
# Remove MANIFEST.txt so Grafana treats the plugin as unsigned rather than
# tampered, allowing GF_PLUGINS_ALLOW_LOADING_UNSIGNED_PLUGINS to apply.
# libgcc-s1 provides libgcc_s.so.1, required by the Rust-compiled sqlite_protobuf extension.
USER root
RUN apt-get update && apt-get install -y libgcc-s1 && rm -rf /var/lib/apt/lists/*

USER grafana
RUN grafana-cli plugins install frser-sqlite-datasource

COPY --from=plugin-builder \
    /plugin/gpx_sqlite-datasource_linux_amd64 \
    /var/lib/grafana/plugins/frser-sqlite-datasource/gpx_sqlite-datasource_linux_amd64

RUN rm /var/lib/grafana/plugins/frser-sqlite-datasource/MANIFEST.txt

ENV GF_PLUGINS_ALLOW_LOADING_UNSIGNED_PLUGINS=frser-sqlite-datasource

# Provision the datasource and dashboard automatically.
COPY grafana_demo/provisioning/ /etc/grafana/provisioning/
