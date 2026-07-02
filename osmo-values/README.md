# OSMO local deployment — Helm values

Helm values for a **local, single-cluster OSMO deployment** following the
[local deployment guide](https://nvidia.github.io/OSMO/main/deployment_guide/appendix/deploy_local.html).

- `service.yaml` — values for the `osmo/service` chart (core service, UI, gateway, postgres/redis/localstack).
- `backend-operator.yaml` — values for the `osmo/backend-operator` chart (backend listener + worker).

Both are installed into the `osmo` namespace:

```bash
helm upgrade --install osmo                 osmo/service          -n osmo -f service.yaml
helm upgrade --install osmo-backend-operator osmo/backend-operator -n osmo -f backend-operator.yaml
```

> **This is a NO-AUTH deployment**, and the backend-operator requires a specific
> `loginMethod` + a minted access-token secret to come up. The full rationale and the
> hard-won learnings (why `password` and `--method dev` both crash, how to mint the
> `backend-operator-token` secret, token expiry) live in the workspace
> [`../README.md`](../README.md). **Read it before installing the backend-operator chart.**
