# Share the GenVM image layer between backend services

- Status: accepted
- Date: 2026-07-31

## Context

Studio runs GenVM from both the JSON-RPC service and the consensus worker. The
services previously acquired and finalized the same GenVM tree in independent
Dockerfiles. Their application images shared the source-build blob but contained
different copies of the roughly 1.061 GB finalized runtime layer. A cold E2E
runner therefore transferred about 3.64 GB of unique image data before it could
start Studio.

Studio supports three GenVM acquisition modes and must retain all of them:

- `prebuilt` consumes the GenVM tree produced by the cross-repository E2E build.
- `source` builds the exact `GENVM_REF` with Nix.
- `release` downloads the exact `GENVM_TAG`, or the repository's default pin.

## Decision

JSON-RPC and the consensus worker are targets in `docker/Dockerfile.backend`.
They inherit one `genvm-runtime` stage and one `service-base` stage. GenVM is
acquired, finalized, and ownership-normalized once before either service target
diverges. Both targets use explicit UID/GID 999, preserving the existing cache
volume ownership while keeping the shared layer byte-identical. Each small
service target renames that account back to its existing public process identity
(`backend-user` or `worker-user`).

Compose builds both targets in one BuildKit graph. The E2E publisher stores the
resulting images in the same ECR repository, where their common layer blobs are
content-addressed and stored once. Pulling both images onto one runner likewise
downloads each common blob once.

A separate GenVM base image was rejected. It would require another published
image, tag-retention policy, release dependency, and local Compose bootstrap
step without improving E2E reuse over a common parent stage.

## Consequences

- A GenVM source SHA or release tag invalidates one shared acquisition stage.
- Backend source and Python dependency changes do not invalidate that GenVM
  stage.
- Worker-only manager thread tuning remains a small child layer.
- No standalone ECR tag is added. Existing `cache-*` and `layercache-*` image
  lifecycle rules continue to own all referenced blobs.
- Based on the measured images at the time of this decision, expected cold
  unique transfer falls from about 3.64 GB to about 2.58 GB.
- Docker workflows must select `prod` or `consensus-worker` explicitly from the
  shared Dockerfile.
- The two service images remain independently deployable and versioned.
