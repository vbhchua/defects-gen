# defects-gen — Deployment Learnings

The four hard-won fixes behind this deployment, each *symptom → root cause → fix*. The full
chronological forensics live in [PROGRESS.md](../PROGRESS.md); the measured costs in
[TIMINGS.md](../TIMINGS.md); the values they landed in under [`osmo-values/`](../osmo-values/).

---

## 1. The backend-operator must use `loginMethod: token`

The main gotcha of this deployment. The `backend-operator` chart authenticates to OSMO,
and the login method matters.

### Symptom

The `osmo-backend-operator-*-listener` and `-worker` pods `CrashLoopBackOff` with:

```
requests.exceptions.MissingSchema: Invalid URL 'None': No scheme supplied. Perhaps you meant https://None?
```

### Root cause

The operator was configured with `global.loginMethod: password`. Password login performs
an **OAuth2 resource-owner-password grant**, which needs an IdP `token_endpoint`. It
resolves the endpoint as `config.token_endpoint or fetch_login_info(host)['token_endpoint']`.
In this no-auth deployment that value is `null`, so `requests.post(None, …)` throws
`MissingSchema`. OSMO's own auth service has **no password grant** — password login can
only ever work against an external IdP. So `loginMethod: password` is fundamentally wrong
for a local no-auth cluster.

### Why `--method dev` is NOT the fix

The obvious idea — make the operator use dev login too — does **not** work in-cluster. In
the operator code, `method == 'dev'` is overloaded: it selects dev login *and* forces the
Kubernetes client to load a **local kubeconfig** (`kube_config.load_kube_config()`), because
`dev` is meant for a developer running the operator on their laptop. Inside the pod there is
no kubeconfig, so you just trade one crash for another:

```
kubernetes.config.config_exception.ConfigException: Invalid kube-config file. No configuration found.
```

(Non-dev methods correctly call `load_incluster_config()`.)

### The fix: token login

Use `global.loginMethod: token`. Token login hits OSMO's **native** endpoint
`<serviceUrl>/api/auth/jwt/access_token` (independent of any external IdP) to exchange an
OSMO access token for a short-lived JWT, and — because `method` is not `dev` — it keeps
**in-cluster** Kubernetes config. Relevant values:

```yaml
global:
  loginMethod: token
  accountTokenSecret: backend-operator-token   # secret holding the access token
  accountTokenSecretKey: token                 # key within that secret
```

The chart mounts `secret/backend-operator-token[token]` at `/opt/osmo/secrets/token.txt`
and passes `--token_file … --login_method token`.

### Creating the access token

The `backend-operator` user is created by `service.yaml`'s `defaultAdmin` (with role
`osmo-admin`). Mint a long-lived access token for it and store it in the secret the chart
expects. Because `authz` is disabled and the gateway grants `osmo-admin`, the admin token
API can be called directly:

```bash
# Mint (returns the raw token exactly once) and pipe straight into a secret —
# avoid printing the token to a terminal/log.
TOKEN=$(kubectl run mint --rm -i --restart=Never -n osmo \
  --image=curlimages/curl:latest --quiet -- \
  -s -X POST 'http://quick-start.osmo.svc.cluster.local/api/auth/user/backend-operator/access_token/backend-operator-auto?expires_at=2027-06-30' \
  | tr -d '"')

kubectl create secret generic backend-operator-token -n osmo \
  --from-literal=token="$TOKEN" \
  --dry-run=client -o yaml | kubectl apply -f -

helm upgrade --install osmo-backend-operator osmo/backend-operator \
  -n osmo -f osmo-values/backend-operator.yaml --wait --timeout 3m
```

Notes:

- Token names are unique per user; if `backend-operator-auto` already exists, delete it
  first (`DELETE /api/auth/user/backend-operator/access_token/backend-operator-auto`) since
  the raw value is only shown at creation time.
- `expires_at` (YYYY-MM-DD) is capped by the service's `max_token_duration`. **The token
  expires** (here `2027-06-30`); when it lapses, the operator will fail to log in again —
  re-mint and update the secret.

### Verifying

```bash
kubectl get pods -n osmo | grep backend-operator     # both 1/1 Running, 0 restarts
kubectl logs -n osmo deploy/osmo-backend-operator-osmo-backend-listener | grep 'Successfully connected'
kubectl logs -n osmo deploy/osmo-backend-operator-osmo-backend-worker   | grep 'Completed job'
```

Healthy listener connects the control/pod/node/event/heartbeat WebSocket streams; the
worker connects and completes `BackendSynchronizeQueues` jobs.

---

## 2. POD_TEMPLATE is ConfigMap-managed — patch via Helm values, not the CLI

The defect-image-generation (DIG) workflows require two node-level mounts on the user
container, enforced by a preflight gate:

- **`/usr/share/nvidia/nvoptix.bin`** — the OptiX denoiser binary, hostPath-mounted
  (present on the compute node `osmo-worker5`, which has the RTX PRO 6000 GPU). Without
  it Kit silently degrades to noisy path tracing.
- **`/dev/shm` ≥ 16 GiB** — a memory-backed `emptyDir`, for Kit ray-tracer buffers and
  torchrun shared memory. Undersized → in-pod preflight fails / torchrun OOMs.

### The gotcha

`osmo config update POD_TEMPLATE --file …` fails with **HTTP 409**:

```
Configs are managed by ConfigMap and cannot be modified via CLI/API.
Update the Helm values and redeploy instead.
```

This deployment sets `services.configFile.enabled: true` / `services.configs.enabled: true`,
so all OSMO configs (POD_TEMPLATE, POOL, SERVICE, …) are rendered from `service.yaml` into a
ConfigMap. `osmo config show` still **reads** fine, but **writes** are rejected. The durable
fix is to edit the values and redeploy.

### The fix

The mounts live in `services.configs.podTemplates.default_user` in
[`osmo-values/service.yaml`](../osmo-values/service.yaml). Note the full container spec
(name + resources) is repeated there because **Helm replaces list values** (`containers`)
rather than deep-merging them — omitting `resources` would drop the templated
`{{USER_CPU}}` / `{{USER_GPU}}` limits. After editing:

```bash
helm upgrade --install osmo osmo/service -n osmo -f osmo-values/service.yaml --wait --timeout 4m
osmo config show POD_TEMPLATE | jq '.default_user.spec.volumes'   # confirm nvoptix + dshm
```

The compute node is pinned via `default_compute`'s `nodeSelector: {node_group: compute}`,
so the merged user pod always lands on the node that actually has `nvoptix.bin`.

---

## 3. Workflow task pods need the in-cluster S3 env (path-style addressing)

The service deployment sets `AWS_ENDPOINT_URL_S3`, `AWS_S3_FORCE_PATH_STYLE=true`, and
`AWS_DEFAULT_REGION` on its pods — but **workflow task pods do not inherit these**; they get
their environment from `POD_TEMPLATE`. Without them, the OSMO runtime's S3 client (in both the
`osmo-ctrl` sidecar and the user container) falls back to **virtual-hosted addressing** and
tries to reach `<bucket>.minio.osmo:9000` (bucket name as a subdomain), which does
not resolve.

### Symptom (nasty, indirect)

Every data-touching workflow fails with exit `2137`; `osmo_user` panics
`Failed to parse response: EOF`. The real cause is on the ctrl side: it hangs in
*"Validating WRITE access for URI output"* while boto3 retries the unresolvable endpoint against
the **24 h** S3 timeout. Its heap grows ~13 MB/s until the container **OOM-kills**
(`Memory cgroup out of memory`, `shmem-rss:0kB`). The task looks like it's "running for 18
minutes" but **nothing ever downloads** — the user container has zero output bytes and no
download process, just `osmo_exec` waiting for an `ExecStart` that never comes.

### The fix

Add the same three S3 vars to `services.configs.podTemplates.default_compute` — on **both**
the `{{USER_CONTAINER_NAME}}` and `osmo-ctrl` containers — then `helm upgrade`:

```yaml
- {name: AWS_ENDPOINT_URL_S3,     value: http://minio.osmo:9000}
- {name: AWS_S3_FORCE_PATH_STYLE, value: "true"}
- {name: AWS_DEFAULT_REGION,      value: us-east-1}
```

Verify: `osmo config show POD_TEMPLATE | jq '.default_compute.spec.containers[].env'`.

> The same addressing bug also affects the **workstation shell**: the `osmo` DATA
> credential must carry `addressing_style=path` (see the [README](../README.md) quickstart
> step 6), and the shell needs the `minio.osmo` `/etc/hosts` entry + a `svc/minio 9000:9000`
> port-forward for `osmo data list/download` to work locally.

---

## 4. Use MinIO for S3, not the chart's localstack

The chart's bundled `localstack-s3` (a `localstack-persist` fork) failed two ways in
practice (full forensics in [`PROGRESS.md`](../PROGRESS.md), 2026-07-02 sections):

1. **Multipart corruption under load.** Its persist thread write-locks the server every
   10 s; during a large concurrent upload (the 80 GB pretrained bundle) the stalls exceed
   the client timeout, boto3 retries `CompleteMultipartUpload`, and the retry races the
   first attempt's part-cleanup → `NoSuchUpload` / missing-part `InternalError` on every
   multi-GB file. Uploads regress and never finish.
2. **Bucket wiped on host reboot.** The kind config bind-mounted its data dir to the
   host's `/tmp/localstack-s3` — cleared on reboot. Every workflow then fails in ~3 s
   with exit `2014` and `osmo workflow logs` returns HTTP 500 `NoSuchBucket`.

The fix is the standalone MinIO in [`osmo-values/minio.yaml`](../osmo-values/minio.yaml)
(disk-native multipart, data on the `data` node's `/var/lib/minio`, host-mounted to
`/var/lib/osmo-minio` by the kind config). Console:
`kubectl port-forward -n osmo svc/minio 9001:9001` → http://localhost:9001 (`test`/`testtest`).
