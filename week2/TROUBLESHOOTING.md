# Week 2 Troubleshooting Guide

## Problem: ModuleNotFoundError when Docker starts

**Symptoms:**
```
ModuleNotFoundError: No module named 'requests'
ModuleNotFoundError: No module named 'lightgbm'
```

**Root cause:** Missing packages in `week2/backend/requirements.txt`

**Solution:**
```bash
# Verify requirements.txt has these essential packages:
# - fastapi, uvicorn, pandas, pyarrow, numpy
# - lightgbm, requests

# Rebuild Docker image
docker build -f week2/starter/Dockerfile -t demand-api:v2 .

# Test locally
docker run -p 8000:8000 demand-api:v2
curl http://localhost:8000/health
```

---

## Problem: ImagePullBackOff in Kubernetes

**Symptoms:**
```
$ kubectl get pods
NAME                          READY   STATUS             RESTARTS   AGE
demand-api-7c8f4b9d6c-abc12   0/1     ImagePullBackOff   0          2m
```

**Root cause:** GKE can't authenticate to pull Docker image from Artifact Registry

**Solution:**

1. Create artifact registry secret:
```bash
gcloud iam service-accounts keys create /tmp/gke-key.json \
  --iam-account=github-actions@ops-ai-[YOUR-NAME].iam.gserviceaccount.com

kubectl create secret docker-registry artifact-registry-secret \
  --docker-server=us-central1-docker.pkg.dev \
  --docker-username=_json_key \
  --docker-password="$(cat /tmp/gke-key.json)" \
  --docker-email=github-actions@ops-ai-[YOUR-NAME].iam.gserviceaccount.com

rm /tmp/gke-key.json
```

2. Verify secret exists:
```bash
kubectl get secrets
# Should list: artifact-registry-secret
```

3. Verify deployment.yaml includes imagePullSecrets:
```yaml
# In week2/starter/k8s/deployment.yaml:
spec:
  imagePullSecrets:
  - name: artifact-registry-secret
```

4. Delete pods to force re-pull:
```bash
kubectl delete pods -l app=demand-api
kubectl get pods  # Should show new pods pulling image
```

---

## Problem: Pod can't download data from GCS

**Symptoms:**
```
$ kubectl logs <pod-name> -c download-data

Error: (gcloud.storage.cp) error parsing arguments: Cloud Storage bucket ...
```

**Root cause:**
- ConfigMap GCS_BUCKET doesn't match actual bucket name
- Service account doesn't have permission to read GCS

**Solution:**

1. Verify ConfigMap matches your bucket:
```bash
# Check what you actually created
gsutil ls

# Check ConfigMap
kubectl get configmap demand-api-config -o yaml

# Update if needed:
kubectl delete configmap demand-api-config
# Edit week2/starter/k8s/configmap.yaml to use correct bucket
kubectl apply -f week2/starter/k8s/configmap.yaml
```

2. Verify service account has GCS permissions:
```bash
gcloud projects get-iam-policy ops-ai-[YOUR-NAME] \
  --flatten="bindings[].members" \
  --filter="bindings.members:github-actions@"
# Should include: roles/storage.objectViewer
```

3. Restart pod to re-download:
```bash
kubectl delete pods -l app=demand-api
kubectl get pods
```

---

## Problem: Pod initializes but health check fails

**Symptoms:**
```
$ kubectl describe pod <pod-name>

Warning  Unhealthy  Readiness probe failed: Get "http://10.0.0.1:8000/health": dial tcp connect: refused
```

**Root cause:**
- Model file not found at startup
- Data.py has incorrect path to model file

**Solution:**

1. Check pod startup logs:
```bash
kubectl logs <pod-name>
# Look for: "[NYC Cab Analytics] Model loaded" or error messages
```

2. Verify GCS files exist:
```bash
gsutil ls -lh gs://ops-ai-[YOUR-NAME]-data/
# Should list: lgbm_demand_model.txt, demand_enriched.parquet
```

3. Check data.py paths are correct:
```python
# In week2/backend/data.py, should have:
MODEL_PATH = _ROOT / "data" / "processed" / "lgbm_demand_model.txt"
DATA_PATH = _ROOT / "data" / "processed" / "demand_enriched.parquet"
LOOKUP_PATH = _ROOT / "metadata" / "Lookups" / "taxi_zone_lookup.csv"
```

4. Increase readiness probe timeout:
```yaml
# In week2/starter/k8s/deployment.yaml:
readinessProbe:
  initialDelaySeconds: 30  # Give more time for model loading
  periodSeconds: 10
  failureThreshold: 3
```

5. Rebuild and redeploy:
```bash
docker build -f week2/starter/Dockerfile -t demand-api:v2 .
docker push us-central1-docker.pkg.dev/ops-ai-[YOUR-NAME]/docker-repo/demand-api:v2
kubectl set image deployment/demand-api \
  demand-api=us-central1-docker.pkg.dev/ops-ai-[YOUR-NAME]/docker-repo/demand-api:v2
```

---

## Problem: Requests timeout or return empty results

**Symptoms:**
```bash
$ curl "http://$EXTERNAL_IP/api/forecast?zone_id=107&hour=12&dow=2&steps=4"
# Hangs or returns timeout

# Returns empty array:
[]
```

**Root cause:**
- Model prediction is slow
- Missing lag features (using 0 instead of actual history)

**Solution:**

The forecast endpoint computation involves:
1. Building temporal features (~1ms)
2. Model.predict() call (~5-10ms per step)
3. Total for 4 steps: ~20-40ms (acceptable)

If slower, check:
- Pod CPU not throttled: `kubectl describe pod <pod-name>`
- Node has resources: `kubectl describe nodes`

For empty results (`[]`), the zone may not have training data:
```bash
# Only 57 of 263 zones have data: 4,13,24,41,43,45,48,50,68,70,74,75,79,87,88,90,93,100,107,113,...
# Try with a valid zone: zone_id=68

# Or check model loading:
kubectl logs <pod-name> | grep -i "model\|error"
```

---

## Problem: CI/CD fails with "auth" errors

**Symptoms:**
```
GitHub Actions log shows:
Error: Could not authenticate to gcloud
Error: Unable to push to artifact registry
```

**Root cause:**
- GitHub secret `GCP_SA_KEY` is truncated or malformed
- Service account key is invalid

**Solution:**

1. Delete old secret and create new one:
```bash
# In GitHub: Settings → Secrets → Delete GCP_SA_KEY
```

2. Create fresh key:
```bash
gcloud iam service-accounts keys delete [KEY-ID] \
  --iam-account=github-actions@ops-ai-[YOUR-NAME].iam.gserviceaccount.com \
  --quiet

gcloud iam service-accounts keys create key.json \
  --iam-account=github-actions@ops-ai-[YOUR-NAME].iam.gserviceaccount.com
```

3. Add to GitHub:
```bash
# Copy ENTIRE contents of key.json
cat key.json

# Paste into GitHub: Settings → Secrets → New secret
# Name: GCP_SA_KEY
# Value: [paste entire JSON]
```

4. Test:
```bash
git add .
git commit -m "Fix CI/CD"
git push origin main
# Watch GitHub Actions tab
```

---

## Problem: Cluster won't create or is stuck

**Symptoms:**
```
$ gcloud container clusters create operationalizing-ai ...
ERROR: ... RESOURCE_EXHAUSTED

# Or after 10+ minutes:
gcloud container clusters list --project ops-ai-[YOUR-NAME]
# Still shows "PROVISIONING"
```

**Solution:**

1. Wait longer (GKE provisioning can take 5-10 minutes)

2. Check status:
```bash
gcloud container clusters list --project ops-ai-[YOUR-NAME]
gcloud container operations list --project ops-ai-[YOUR-NAME]
```

3. If stuck for >15 minutes, delete and retry:
```bash
gcloud container clusters delete operationalizing-ai \
  --zone us-central1-a \
  --project ops-ai-[YOUR-NAME]

# Wait for deletion, then try again
gcloud container clusters create operationalizing-ai \
  --zone us-central1-a \
  --num-nodes 2 \
  --machine-type n1-standard-2 \
  --project ops-ai-[YOUR-NAME]
```

---

## Quick Diagnostic Checklist

When things aren't working, run this:

```bash
# 1. Check GCP resources
gcloud projects list | grep ops-ai
gsutil ls

# 2. Check cluster
gcloud container clusters list --project ops-ai-[YOUR-NAME]
kubectl get nodes
kubectl get pods -o wide

# 3. Check pod status
kubectl describe pod $(kubectl get pods -o jsonpath='{.items[0].metadata.name}')

# 4. Check logs
kubectl logs <pod-name>
kubectl logs <pod-name> -c download-data  # Init container logs

# 5. Test API
EXTERNAL_IP=$(kubectl get svc demand-api -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
curl http://$EXTERNAL_IP/health

# 6. Check ConfigMap
kubectl get configmap demand-api-config -o yaml

# 7. Check secrets
kubectl get secrets
```

If still stuck, check:
- README Part 2 step-by-step
- GitHub Actions logs (Actions tab)
- GCP Console for resource quotas
