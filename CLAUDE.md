# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Git conventions

- **Commit messages follow [Conventional Commits v1.0.0](https://www.conventionalcommits.org/en/v1.0.0/)**
  (`feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, … with optional scope).
- **Never push to `master` directly.** All changes go through a feature branch and a
  pull request — no exceptions, including docs-only changes.

## What this workspace is

`defects-gen` is an **operations workspace for standing up a local, single-cluster
OSMO deployment** on a [kind](https://kind.sigs.k8s.io/) cluster. It is not the OSMO
source tree — that lives in `OSMO/`, a full clone of the upstream repo with its own
guidance files.

Layout:

- `kind-osmo-cluster-config.yaml` — kind cluster definition (control-plane + 5 workers
  with `node_group` labels; the `service` worker maps host port 80 → NodePort 30080).
- `osmo-values/` — Helm values for the two OSMO charts, plus `README.md` documenting
  the deployment and its hard-won gotchas.
- `OSMO/` — upstream OSMO source. **Has its own `CLAUDE.md` + `AGENTS.md`**; follow
  those when editing OSMO code. Do not treat it as part of this workspace's config.

## Deploying (the whole point of this workspace)

Follow the upstream [local deployment guide](https://nvidia.github.io/OSMO/main/deployment_guide/appendix/deploy_local.html).
The two charts install into the `osmo` namespace:

```bash
# 1. service chart: core service, UI, gateway, postgres/redis/localstack-s3
helm upgrade --install osmo osmo/service -n osmo -f osmo-values/service.yaml

# 2. backend-operator chart: backend listener + worker
#    (requires the access-token secret below to exist first)
helm upgrade --install osmo-backend-operator osmo/backend-operator \
  -n osmo -f osmo-values/backend-operator.yaml --wait --timeout 3m
```

Human login uses **dev** auth (see below):

```bash
osmo login http://quick-start.osmo --method=dev --username=testuser
```

## Critical: this is a NO-AUTH deployment

There is **no identity provider**. `service.yaml` disables `oauth2Proxy` and `authz`,
and the Envoy gateway injects a fixed identity (`user: testuser`, `roles: osmo-admin`,
`allowedPools: default`) on every request. Consequences that trip people up:

- OSMO advertises **no OAuth2 endpoints** — `GET /api/auth/login` returns all-`null`.
  Anything that depends on `token_endpoint` / password grants cannot work.
- Every gateway request is `testuser`/`osmo-admin`. Admin APIs can be called directly.
- **Not for production.**

## Critical: backend-operator must use `loginMethod: token`

This is the deployment's main gotcha (full write-up in `osmo-values/README.md`). The
backend-operator (`backend-operator.yaml`) authenticates to OSMO, and login method
matters:

- `loginMethod: password` → **wrong**. Needs an IdP `token_endpoint`, which is `null`
  here → pods `CrashLoopBackOff` with `requests.exceptions.MissingSchema: Invalid URL 'None'`.
- `--method dev` → **also wrong in-cluster**. `dev` forces `load_kube_config()` (a local
  kubeconfig), which doesn't exist in the pod → `ConfigException: No configuration found`.
- `loginMethod: token` → **correct**. Hits OSMO's native
  `<serviceUrl>/api/auth/jwt/access_token` (no external IdP) and keeps in-cluster K8s config.

Token login needs an access token in the `backend-operator-token` secret. Mint one for
the `backend-operator` admin user (created by `service.yaml`'s `defaultAdmin`) **without
printing it to a log**:

```bash
TOKEN=$(kubectl run mint --rm -i --restart=Never -n osmo \
  --image=curlimages/curl:latest --quiet -- \
  -s -X POST 'http://quick-start.osmo.svc.cluster.local/api/auth/user/backend-operator/access_token/backend-operator-auto?expires_at=2027-06-30' \
  | tr -d '"')

kubectl create secret generic backend-operator-token -n osmo \
  --from-literal=token="$TOKEN" --dry-run=client -o yaml | kubectl apply -f -
```

- Token names are unique per user — if `backend-operator-auto` already exists, DELETE it
  first (the raw value is only shown at creation).
- **The token expires** (currently `2027-06-30`, capped by the service's
  `max_token_duration`). When it lapses the operator can't log in — re-mint and update
  the secret.

## Verifying a healthy deployment

```bash
kubectl get pods -n osmo | grep backend-operator     # both 1/1 Running, 0 restarts
kubectl logs -n osmo deploy/osmo-backend-operator-osmo-backend-listener | grep 'Successfully connected'
kubectl logs -n osmo deploy/osmo-backend-operator-osmo-backend-worker   | grep 'Completed job'
```

## Conventions in the values files

- `hostname` / gateway name is `quick-start.osmo`; in-cluster service URL is
  `http://quick-start.osmo.svc.cluster.local`.
- Storage is **MinIO** (`http://minio.osmo:9000`, bucket `osmo`, creds `test`/`testtest`)
  — every service sets `AWS_ENDPOINT_URL_S3` + path-style addressing. Deployed standalone
  via `kubectl apply -f osmo-values/minio.yaml` (NOT part of the Helm release); data on the
  `data` node at `/var/lib/minio`. It replaced the chart's localstack-s3 (2026-07-02):
  localstack-persist corrupted concurrent multipart uploads and lost buckets on host
  restart. Verify bucket contents in-cluster: `kubectl exec -n osmo deploy/minio -- du -sh
  /data/osmo/<prefix>`.
- Workloads are pinned by `nodeSelector` to `node_group` labels (`service`, `data`,
  `compute`) that match the kind config. Keep the two in sync when changing either.
