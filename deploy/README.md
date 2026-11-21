# Production setup - manual steps

Code, Dockerfile, k8s manifests and CI are all in this repo. These are the one-time manual steps outside the repo, in order. App is served at `https://skills.monashautomation.com`, namespace `skills`.

## 1. GitHub repo

Repo Settings → Actions → General → Workflow permissions → **Read and write permissions** (needed so `deploy.yml` can push images to GHCR and commit the overlay tag bump).

## 2. Authentik - OIDC application + provider

In the Authentik admin UI (`https://auth.monashautomation.com/if/admin/`):

1. **Applications → Providers → Create** → **OAuth2/OIDC Provider**.
    - Name: `skills`
    - Authorization flow: same one directory-service/ticketing use.
    - Client type: **Confidential**
    - Redirect URIs (strict): `https://skills.monashautomation.com/auth/callback/`
    - Scopes: `openid`, `email`, `profile`
    - Save. Copy the **Client ID** and **Client Secret** for step 3.
2. **Applications → Applications → Create**
    - Name: `Skills`, slug **`skills`** (the issuer in `deploy/base/configmap.yaml` is `https://auth.monashautomation.com/application/o/skills/`)
    - Provider: the one you just created
    - Launch URL: `https://skills.monashautomation.com`

## 3. OpenBao - secrets

In the OpenBao web UI:

**a) Enable a KV v2 secrets engine** - Secrets Engines → Enable new engine → KV, version 2, path `skills`.

**b) Create secrets** in the `skills/` engine (generate _random_ values with `openssl rand -hex 32`):

- Path `skills/web`:

    | Key                       | Value                                                                       |
    | ------------------------- | --------------------------------------------------------------------------- |
    | `DJANGO_SECRET_KEY`       | _random_                                                                    |
    | `DATABASE_URL`            | `postgres://skills:<postgres-password>@skills-postgres:5432/skills`         |
    | `SKILL_TRACKER_TOKEN`     | _random_ - the Discord bot sends this as its API bearer token               |
    | `DIRECTORY_API_KEY`       | API key minted in directory-service (read scope)                            |
    | `AUTHENTIK_CLIENT_ID`     | from step 2.1                                                               |
    | `AUTHENTIK_CLIENT_SECRET` | from step 2.1                                                               |

- Path `skills/postgres`:

    | Key                 | Value    |
    | ------------------- | -------- |
    | `POSTGRES_USER`     | `skills` |
    | `POSTGRES_PASSWORD` | _random_ |

    URL-encode the password in `DATABASE_URL` if it contains special characters (`openssl rand -hex` avoids this).

**c) Policy** - Policies → ACL Policies → Create, name `skills-read`:

```hcl
path "skills/data/*" {
  capabilities = ["read"]
}
```

**d) Kubernetes auth role** - Access → Kubernetes → (existing k8s auth method) → Roles → Create role:

- Role name: `skills`
- Bound service account names: `skills-eso`
- Bound service account namespaces: `skills`
- Generated Token's Policies: `skills-read`
- TTL: same as the other apps' roles (commonly 24h)

CLI equivalent if the UI lacks it: `bao write auth/kubernetes/role/skills ...`.

## 4. Cluster - namespace + GHCR pull secret

The `skills` namespace is auto-created by ArgoCD (`CreateNamespace=true`), but the manifests reference `ghcr-pull-secret` (same as directory-service), which is namespace-scoped. Copy it once the namespace exists (wait for the first sync, or `kubectl create namespace skills`):

```bash
kubectl get secret ghcr-pull-secret -n directory -o json \
  | jq 'del(.metadata.namespace, .metadata.resourceVersion, .metadata.uid, .metadata.creationTimestamp, .metadata.ownerReferences, .metadata.managedFields, .metadata.annotations)' \
  | kubectl apply -n skills -f -
```

Alternative: make the `ghcr.io/monashautomation/skill-tracker` package public (GitHub → org Packages → package settings → Change visibility); the pull secret is then unnecessary but harmless. Note the first push creates the package as private even though the repo is public.

## 5. DNS

Point `skills.monashautomation.com` at the same ingress IP / load balancer as `directory.monashautomation.com` (A/CNAME record; nothing to do if `*.monashautomation.com` is a wildcard).

## 6. Register the app with ArgoCD

```bash
kubectl apply -f argocd/application.yaml
```

ArgoCD syncs `deploy/overlays/prod` and picks up the images pushed by `.github/workflows/deploy.yml` on every push to `main`.

## 7. First deploy checklist

1. Push to `main` → Actions builds+pushes `ghcr.io/monashautomation/skill-tracker:sha-<sha>` and bumps `deploy/overlays/prod/kustomization.yaml`. (Until the first bump the overlay tag is `latest`, which does not exist - the first sync fails until CI has run once.)
2. ArgoCD syncs: `skills-postgres` + secrets → `skills-migrate` Job (`manage.py migrate`) → `skills-web` rolls out → ingress/cert.
3. Visit `https://skills.monashautomation.com`, sign in with Authentik.
4. Populate members immediately instead of waiting up to 15 min: `kubectl create job --from=cronjob/skills-sync-directory skills-sync-manual -n skills`.
