<div align="center">

<pre>
██████╗ ███████╗███████╗███████╗ ██████╗████████╗███████╗       ██████╗ ███████╗███╗   ██╗
██╔══██╗██╔════╝██╔════╝██╔════╝██╔════╝╚══██╔══╝██╔════╝      ██╔════╝ ██╔════╝████╗  ██║
██║  ██║█████╗  █████╗  █████╗  ██║        ██║   ███████╗█████╗██║  ███╗█████╗  ██╔██╗ ██║
██║  ██║██╔══╝  ██╔══╝  ██╔══╝  ██║        ██║   ╚════██║╚════╝██║   ██║██╔══╝  ██║╚██╗██║
██████╔╝███████╗██║     ███████╗╚██████╗   ██║   ███████║      ╚██████╔╝███████╗██║ ╚████║
╚═════╝ ╚══════╝╚═╝     ╚══════╝ ╚═════╝   ╚═╝   ╚══════╝       ╚═════╝ ╚══════╝╚═╝  ╚═══╝
</pre>

<h3>🏭&nbsp; synthetic defect images for AOI — on your own GPU</h3>

<p>
<em>Testing <b>NVIDIA&nbsp;Cosmos</b> for defects generation: a reproducible, single-host
<a href="https://github.com/NVIDIA/OSMO">OSMO</a> deployment on
<a href="https://kind.sigs.k8s.io/">kind</a> that runs the full
<b>defect-image-generation (DIG)</b> pipeline —<br/>
AnomalyGen inference, labeling, and every hard-won fix it took to get there.</em>
</p>

<p>
<a href="https://github.com/NVIDIA/OSMO"><img src="https://img.shields.io/badge/NVIDIA-OSMO-76B900?logo=nvidia&logoColor=white" alt="NVIDIA OSMO"></a>
<a href="https://github.com/NVIDIA/skills/tree/main/skills/physical-ai-defect-image-generation"><img src="https://img.shields.io/badge/Cosmos-AnomalyGen%202B-76B900?logo=nvidia&logoColor=white" alt="Cosmos AnomalyGen 2B"></a>
<a href="https://kind.sigs.k8s.io/"><img src="https://img.shields.io/badge/kubernetes-kind-326CE5?logo=kubernetes&logoColor=white" alt="kind"></a>
<a href="https://helm.sh"><img src="https://img.shields.io/badge/deploy-Helm-0F1689?logo=helm&logoColor=white" alt="Helm"></a>
<a href="https://min.io"><img src="https://img.shields.io/badge/S3-MinIO-C72E49?logo=minio&logoColor=white" alt="MinIO"></a>
<img src="https://img.shields.io/badge/GPU-1×%20RTX%20PRO%206000-76B900" alt="1× RTX PRO 6000">
<img src="https://img.shields.io/badge/E2E%20verified-2026--07--02-success" alt="E2E verified">
<a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue" alt="MIT"></a>
</p>

<code>kind cluster&nbsp;→&nbsp;MinIO S3&nbsp;→&nbsp;OSMO charts&nbsp;→&nbsp;DIG setup (81&nbsp;GB)&nbsp;→&nbsp;AnomalyGen&nbsp;→&nbsp;labeled defect images</code>

</div>

---

## 🎨 The output

Real samples from this deployment's first verified run (metal surface, Day 1 manual-ROI,
pretrained passthrough — 30 labeled images in ~31 min, of which ~8 min was GPU time):

<div align="center">

**clean input → defect mask → generated defect** (`MT_Crack`)

<img src="docs/images/MT_Crack_original.png" width="30%" alt="clean input"> <img src="docs/images/MT_Crack_mask.png" width="30%" alt="defect mask"> <img src="docs/images/MT_Crack.png" width="30%" alt="generated crack defect">

**one generated sample per defect class**

| MT_Blowhole | MT_Break | MT_Crack | MT_Fray | MT_Uneven |
|---|---|---|---|---|
| <img src="docs/images/MT_Blowhole.png" width="160"> | <img src="docs/images/MT_Break.png" width="160"> | <img src="docs/images/MT_Crack.png" width="160"> | <img src="docs/images/MT_Fray.png" width="160"> | <img src="docs/images/MT_Uneven.png" width="160"> |

</div>

## ✨ What's in the box

|   |   |
|---|---|
| 🏗️&nbsp; **Deploys** | A 6-node **kind** cluster (GPU passthrough, `node_group` scheduling) running the **OSMO** service + backend-operator Helm charts in no-auth dev mode. |
| 🪣&nbsp; **Stores** | A standalone **MinIO** S3 backend on the host's 1 TB volume — survives reboots, handles 80 GB multipart uploads at 60–300 MB/s (the chart's bundled localstack does neither; see Learnings). |
| 🎨&nbsp; **Generates** | **Cosmos AnomalyGen 2B** defect images via NVIDIA's [DIG skill](https://github.com/NVIDIA/skills/tree/main/skills/physical-ai-defect-image-generation): metal-surface Day 1 verified end-to-end; PCBA / glass ready to go. |
| 🏷️&nbsp; **Labels** | Every generated image ships with masks, crops, annotated overlays, `SDG_result.csv`, and DAFT-v3 labeling artifacts. |
| 🔬&nbsp; **Diagnoses** | [`PROGRESS.md`](PROGRESS.md) — the full bring-up forensics: every failure signature (OOM spirals, bucket wipes, token traps, multipart corruption) with root cause and fix. |
| ⏱️&nbsp; **Benchmarks** | [`TIMINGS.md`](TIMINGS.md) — measured wall clocks, phase breakdowns, throughputs, and a localstack-vs-MinIO head-to-head. |

## 🚀 Replicating from scratch

Prerequisites on the host: `docker`, [`kind`](https://kind.sigs.k8s.io/), `kubectl`,
`helm`, the `osmo` CLI, an NVIDIA GPU with drivers + the
[NVIDIA container toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
(the compute node passes GPUs through via the `nvidia-container-devices` mount in the kind
config), and `/usr/share/nvidia/nvoptix.bin` present on the host (ships with the driver).

```bash
git clone https://github.com/vbhchua/defects-gen && cd defects-gen
cp .env.example .env    # then edit: real HF *user access* token (see .env.example notes)

# 1. Cluster + GPU operator
kind create cluster --config kind-osmo-cluster-config.yaml
helm install gpu-operator nvidia/gpu-operator -n gpu-operator --create-namespace  # wait for nvidia-cuda-validator Completed

# 2. S3 backend (MinIO) + bucket — BEFORE the OSMO charts
kubectl create namespace osmo
kubectl apply -f osmo-values/minio.yaml
kubectl rollout status deploy/minio -n osmo
kubectl run mc-setup --rm -i --restart=Never -n osmo --image=minio/mc --command -- \
  sh -c 'mc alias set m http://minio.osmo.svc.cluster.local:9000 test testtest && mc mb -p m/osmo'

# 3. OSMO service chart (core service, UI, gateway, postgres, redis)
helm repo add osmo https://nvidia.github.io/OSMO/helm-charts && helm repo update
helm upgrade --install osmo osmo/service -n osmo -f osmo-values/service.yaml --wait --timeout 4m

# 4. Backend-operator token secret (see "backend-operator login method" below), then:
helm upgrade --install osmo-backend-operator osmo/backend-operator \
  -n osmo -f osmo-values/backend-operator.yaml --wait --timeout 3m

# 5. Login + host DNS for the gateway and MinIO
echo "127.0.0.1 quick-start.osmo minio.osmo" | sudo tee -a /etc/hosts
osmo login http://quick-start.osmo --method=dev --username=testuser
kubectl port-forward -n osmo svc/minio 9000:9000 &   # per-session; makes `osmo data` work from the shell

# 6. Credentials for DIG workflows
set -a; . ./.env; set +a
osmo credential set hf-token --type GENERIC --payload token="$HF_TOKEN"
osmo credential set osmo --type DATA --payload endpoint=s3://osmo \
  access_key_id=test access_key=testtest override_url=http://minio.osmo:9000 \
  region=us-east-1 addressing_style=path
```

Then run the DIG pipeline via NVIDIA's
[**physical-ai-defect-image-generation** skill](https://github.com/NVIDIA/skills/tree/main/skills/physical-ai-defect-image-generation)
(install it from [NVIDIA/skills](https://github.com/NVIDIA/skills); everything in this
repo's DIG runs — workflow YAMLs, cookbooks, preflight scripts — comes from that skill):
the setup workflows (`setup_metal.yaml` + `setup_pretrained.yaml`, ~1 h for the 80 GB
pretrained bundle), then the flow submit. `PROGRESS.md` §"STATUS" has the canonical metal
Day 1 submit block with all cluster-specific knobs (notably `infer_memory=48Gi` on a
62 GiB node and `--set dig_url_root=s3://osmo/dig`).

## ⏱️ How long things take

Measured on 1× RTX PRO 6000 / 62 GiB host — full tables in [`TIMINGS.md`](TIMINGS.md):

| Stage | Wall clock |
|---|---|
| Metal setup (checkpoint + dataset → S3) | **~3 min** |
| Pretrained bundle setup (80.6 GB: HF → assemble → S3) | **~56 min** |
| Day 1 inference run (30 labeled images, passthrough) | **~31 min** (≈22 min fixed input download + ~8 min GPU) |

> The input download is a fixed cost per run — larger `num_sdg` amortizes it
> (300 images ≈ 1.7 h, not 10× the 31-min run).

## 🗂️ Layout

- `kind-osmo-cluster-config.yaml` — kind cluster definition (control-plane + 5 workers
  with `node_group` labels; the `service` worker maps host port 80 → NodePort 30080; the
  `data` worker host-mounts `/var/lib/osmo-minio` for durable S3 storage).
- `osmo-values/` — Helm values for the two OSMO charts (`service.yaml`,
  `backend-operator.yaml`), the standalone MinIO manifest (`minio.yaml`), and
  chart-specific install notes.
- `PROGRESS.md` — the chronological bring-up log: every failure, root cause, and fix on
  the way to the first successful DIG run. **Read this when something breaks** — most
  failure signatures on this stack are already diagnosed there.
- `TIMINGS.md` — all measured durations and throughputs (per-workflow wall clocks, phase
  breakdowns, failure time-to-detect, localstack-vs-MinIO comparison) for benchmarking
  and presentations.
- `docs/images/` — sample generated defect images (the showcase above).
- `.env.example` — template for the git-ignored `.env` (Hugging Face token).
- `OSMO/` — *(git-ignored)* an optional local clone of https://github.com/NVIDIA/OSMO
  for source reference. Not part of the deployment config; clone it yourself if needed.

## 📦 Deploying (chart summary)

Both charts install into the `osmo` namespace:

```bash
# 1. service chart: core service, UI, gateway, postgres/redis
#    (S3 is the standalone MinIO above — the chart's localstack is disabled)
helm upgrade --install osmo osmo/service -n osmo -f osmo-values/service.yaml

# 2. backend-operator chart: backend listener + worker
#    (requires the access-token secret from the fix below to exist first)
helm upgrade --install osmo-backend-operator osmo/backend-operator \
  -n osmo -f osmo-values/backend-operator.yaml --wait --timeout 3m
```

Human login uses **dev** auth (see "No-auth deployment" below):

```bash
osmo login http://quick-start.osmo --method=dev --username=testuser
```

## 🔓 This is a NO-AUTH deployment

There is **no identity provider (IdP)**. `service.yaml` disables `oauth2Proxy` and `authz`,
and the Envoy gateway injects a fixed identity on every request:

```yaml
gateway:
  envoy:
    defaultIdentity:      # envoy injects a fixed identity on every request
      user: testuser
      roles: osmo-admin
      allowedPools: default
  oauth2Proxy:
    enabled: false
  authz:
    enabled: false
```

Consequences:

- The core service has no auth endpoints configured, so OSMO advertises **no OAuth2
  endpoints** — `GET /api/auth/login` returns all-`null`
  (`device_endpoint`, `token_endpoint`, `browser_endpoint`, … are all `null`). Anything
  that depends on `token_endpoint` / a password grant cannot work.
- Every gateway request is treated as `testuser` with role `osmo-admin` (authz disabled +
  envoy `defaultIdentity`), so admin APIs can be called directly.
- Humans log in with **dev** auth (`osmo login … --method=dev --username=testuser`).

> ⚠️ Not for production — no real authentication.

---

## 🧠 Learning: the backend-operator login method

The main gotcha of this deployment. The `backend-operator` charts authenticate to OSMO,
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

## 🧠 Learning: POD_TEMPLATE is ConfigMap-managed — patch via Helm values, not the CLI

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
[`osmo-values/service.yaml`](osmo-values/service.yaml). Note the full container spec
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

## 🧠 Learning: workflow task pods need the in-cluster S3 env (path-style addressing)

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
> credential must carry `addressing_style=path` (see quickstart step 6), and the shell
> needs the `minio.osmo` `/etc/hosts` entry + a `svc/minio 9000:9000` port-forward for
> `osmo data list/download` to work locally.

---

## 🧠 Learning: use MinIO for S3, not the chart's localstack

The chart's bundled `localstack-s3` (a `localstack-persist` fork) failed two ways in
practice (full forensics in `PROGRESS.md`, 2026-07-02 sections):

1. **Multipart corruption under load.** Its persist thread write-locks the server every
   10 s; during a large concurrent upload (the 80 GB pretrained bundle) the stalls exceed
   the client timeout, boto3 retries `CompleteMultipartUpload`, and the retry races the
   first attempt's part-cleanup → `NoSuchUpload` / missing-part `InternalError` on every
   multi-GB file. Uploads regress and never finish.
2. **Bucket wiped on host reboot.** The kind config bind-mounted its data dir to the
   host's `/tmp/localstack-s3` — cleared on reboot. Every workflow then fails in ~3 s
   with exit `2014` and `osmo workflow logs` returns HTTP 500 `NoSuchBucket`.

The fix is the standalone MinIO in `osmo-values/minio.yaml` (disk-native multipart,
data on the `data` node's `/var/lib/minio`, host-mounted to `/var/lib/osmo-minio` by the
kind config). Console: `kubectl port-forward -n osmo svc/minio 9001:9001` →
http://localhost:9001 (`test`/`testtest`).

---

## 🙏 Credits

- 🎨 DIG pipeline (workflow YAMLs, cookbooks, preflight scripts, monitoring discipline):
  NVIDIA's [physical-ai-defect-image-generation](https://github.com/NVIDIA/skills/tree/main/skills/physical-ai-defect-image-generation)
  skill from the [NVIDIA/skills](https://github.com/NVIDIA/skills) repository.
- 🏗️ OSMO platform + Helm charts: [NVIDIA/OSMO](https://github.com/NVIDIA/OSMO) and its
  [local deployment guide](https://nvidia.github.io/OSMO/main/deployment_guide/appendix/deploy_local.html).
