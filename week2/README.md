# Week 2 — Deployment & CI/CD

## Before You Start

1. Check [REQUIREMENTS.md](REQUIREMENTS.md) for system setup
2. Read [READING.md](READING.md) for operational context
3. If files appear truncated (Git LFS pointers), run: `git lfs pull`

## Assignment

Deploy the pre-trained model API to GKE. Set up GitHub Actions to build, test, and deploy on push. You are completely self-sufficient - you create your own GCP project, buckets, and credentials.

**Deliverables:**

- Live API endpoint (responds to health checks, serves predictions)
- GitHub repository with working CI/CD pipeline
- Architecture diagram showing GitHub → Artifact Registry → GKE flow
- Design report (1-2 pages) explaining operational decisions
- Screenshot of GKE cluster deletion (you must clean up after yourself)

## Your Repo Structure

```
week2/
├── backend/           (pre-written: main.py, data.py, requirements.txt)
├── data/
│   └── demand_enriched.parquet (pre-loaded, you upload to GCS)
├── model/
│   └── lgbm_demand_model.txt (pre-trained LightGBM)
├── starter/           (you edit these files)
│   ├── Dockerfile
│   ├── k8s/           (deployment.yaml, service.yaml, configmap.yaml)
│   └── .github/workflows/  (ci.yml, cd.yml)
├── README.md          (this file)
├── READING.md
└── REQUIREMENTS.md
```

---

# PART 0: GCP SETUP

You will create your own GCP project, bucket, and credentials. This costs money (~$2-5 for the week). You are responsible for cleaning up.

## Before You Start

Make sure you have gcloud installed and authenticated:

```bash
# Check gcloud is installed
gcloud --version

# If you just installed it, authenticate
gcloud auth login

# Verify you're authenticated
gcloud config list
```

## Step 1: Create GCP Project

```bash
# Create project (replace [YOUR-NAME] with something unique, e.g., alice, student-001)
gcloud projects create ops-ai-[YOUR-NAME] --set-as-default

# This name appears in all your GCP resources: bucket, project ID, cluster names
# Example patterns:
#   ops-ai-alice → bucket: gs://ops-ai-alice-data
#   ops-ai-student-001 → bucket: gs://ops-ai-student-001-data
```

**Important:** Use the project ID consistently throughout all steps. Choose a name you'll remember.

## Step 2: Set Up Billing

GCP requires billing enabled to create resources:

```bash
# Go to https://console.cloud.google.com/billing
# OR
gcloud billing accounts list
gcloud beta billing projects link ops-ai-[YOUR-NAME] --billing-account=[ACCOUNT-ID]
```

If you don't see billing accounts, enable it via the web console.

## Step 3: Enable Required APIs

```bash
gcloud services enable container.googleapis.com
gcloud services enable artifactregistry.googleapis.com
gcloud services enable compute.googleapis.com
gcloud services enable storage-api.googleapis.com
```

## Step 4: Create GCS Bucket

This bucket stores your model and data (what the deployed pods will read):

```bash
gsutil mb gs://ops-ai-[YOUR-NAME]-data

# Verify:
gsutil ls
```

## Step 5: Upload Data and Model

Copy provided data/model to your bucket:

```bash
# From repo root:
gsutil cp week2/data/demand_enriched.parquet gs://ops-ai-[YOUR-NAME]-data/
gsutil cp week2/model/lgbm_demand_model.txt gs://ops-ai-[YOUR-NAME]-data/
gsutil cp week2/backend/zone_hour_avg_fare.parquet gs://ops-ai-[YOUR-NAME]-data/
gsutil cp week2/backend/taxi_zones.geojson gs://ops-ai-[YOUR-NAME]-data/

# Verify all files uploaded:
gsutil ls -lh gs://ops-ai-[YOUR-NAME]-data/
```

Your bucket now contains all files that the API pods will download on startup.

## Step 6: Create Artifact Registry

For Docker image storage (similar to Docker Hub):

```bash
gcloud artifacts repositories create docker-repo \
  --repository-format=docker \
  --location=us-central1

# Verify:
gcloud artifacts repositories list
```

## Step 7: Create Service Account for GitHub Actions & GKE

GitHub needs credentials to deploy to your GCP project, and GKE needs credentials to pull Docker images:

```bash
# Create service account
gcloud iam service-accounts create github-actions

# Grant permissions (allow deployment to GKE, Artifact Registry, GCS)
gcloud projects add-iam-policy-binding ops-ai-[YOUR-NAME] \
  --member=serviceAccount:github-actions@ops-ai-[YOUR-NAME].iam.gserviceaccount.com \
  --role=roles/container.developer

gcloud projects add-iam-policy-binding ops-ai-[YOUR-NAME] \
  --member=serviceAccount:github-actions@ops-ai-[YOUR-NAME].iam.gserviceaccount.com \
  --role=roles/artifactregistry.writer

gcloud projects add-iam-policy-binding ops-ai-[YOUR-NAME] \
  --member=serviceAccount:github-actions@ops-ai-[YOUR-NAME].iam.gserviceaccount.com \
  --role=roles/storage.objectViewer

# Also grant permissions to pull images from Artifact Registry
gcloud projects add-iam-policy-binding ops-ai-[YOUR-NAME] \
  --member=serviceAccount:github-actions@ops-ai-[YOUR-NAME].iam.gserviceaccount.com \
  --role=roles/artifactregistry.reader
```

## Step 8: Create and Download Service Account Key

This key allows GitHub to authenticate:

```bash
gcloud iam service-accounts keys create key.json \
  --iam-account=github-actions@ops-ai-[YOUR-NAME].iam.gserviceaccount.com

# Verify key was created:
ls -la key.json

# IMPORTANT: Add to .gitignore (never commit credentials!)
echo "key.json" >> .gitignore
git add .gitignore
git commit -m "Add key.json to .gitignore"
```

---

# PART 1: Create GKE Cluster

Now create the Kubernetes cluster where your API will run:

```bash
gcloud container clusters create operationalizing-ai \
  --zone us-central1-a \
  --num-nodes 2 \
  --machine-type n1-standard-2 \
  --enable-autoscaling \
  --min-nodes 2 \
  --max-nodes 5 \
  --project ops-ai-[YOUR-NAME]

# This takes ~3-5 minutes
```

Get credentials so `kubectl` can talk to your cluster:

```bash
gcloud container clusters get-credentials operationalizing-ai \
  --zone us-central1-a \
  --project ops-ai-[YOUR-NAME]

# Verify:
kubectl get nodes  # Should show 2 nodes
```

---

# PART 2: Prepare Configuration Files

## Step 1: Create Artifact Registry Secret

Before applying Kubernetes manifests, create a secret for pulling Docker images from Artifact Registry:

```bash
# Create service account key for GKE to use (if you haven't already)
gcloud iam service-accounts keys create /tmp/gke-key.json \
  --iam-account=github-actions@ops-ai-[YOUR-NAME].iam.gserviceaccount.com

# Create Kubernetes secret from the key
kubectl create secret docker-registry artifact-registry-secret \
  --docker-server=us-central1-docker.pkg.dev \
  --docker-username=_json_key \
  --docker-password="$(cat /tmp/gke-key.json)" \
  --docker-email=github-actions@ops-ai-[YOUR-NAME].iam.gserviceaccount.com

# Verify secret was created
kubectl get secrets artifact-registry-secret

# Clean up temp file (never commit credentials!)
rm /tmp/gke-key.json
```

## Step 1b: Create GCS Service Account Secret

The init container in the deployment needs to authenticate to GCS to download model files. Create a generic secret from the service account key:

```bash
# Create generic secret for GCS access (init container uses this)
kubectl create secret generic gcs-sa-key \
  --from-file=key.json=key.json

# Verify secret was created
kubectl get secrets gcs-sa-key
```

## Step 2: Edit ConfigMap

File: `week2/starter/k8s/configmap.yaml`

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: demand-api-config
data:
  GCS_BUCKET: "ops-ai-[YOUR-NAME]-data"  # Replace [YOUR-NAME]
```

## Step 3: Edit Deployment Manifest

File: `week2/starter/k8s/deployment.yaml`

Replace `[YOUR-NAME]` in the image path (note the `ops-ai-` prefix):

```yaml
image: us-central1-docker.pkg.dev/ops-ai-[YOUR-NAME]/docker-repo/demand-api:latest
```

Fill in the TODO values:

- `replicas`: 2-3 (for 10+ concurrent requests)
- `imagePullPolicy`: Always (or IfNotPresent)
- `maxSurge` and `maxUnavailable`: 1 each (for safe rolling updates)
- `cpu/memory requests`: 512m / 1Gi
- `cpu/memory limits`: 1000m / 3Gi
- Probe timings (initialDelaySeconds, periodSeconds, failureThreshold)

## Step 4: Edit Service Manifest

File: `week2/starter/k8s/service.yaml`

Fill in the TODO values:

- `type`: LoadBalancer (exposes external IP)
- `selector.app`: demand-api (matches deployment label)
- `port`: 80 (external port)
- `targetPort`: 8000 (container port)

## Step 5: Update CD Workflow

File: `week2/starter/.github/workflows/cd.yml`

Replace all instances of `[YOUR-NAME]` with your project name:

```yaml
env:
  GCP_PROJECT_ID: ops-ai-[YOUR-NAME]      # Your project ID
  ARTIFACT_REGISTRY_REGION: us-central1
  ARTIFACT_REGISTRY_REPO: docker-repo
  IMAGE_NAME: demand-api
  GCP_ZONE: us-central1-a
  CLUSTER_NAME: operationalizing-ai
```

---

# PART 3: Container & Kubernetes

**Work from repo root.**

## Before You Start

Make sure you have the prerequisites ready:

```bash
# Check Docker is running (macOS: make sure Docker.app is open)
docker --version
docker ps  # Should connect without errors

# Check kubectl is configured and can reach your cluster
kubectl get nodes  # Should show 2 nodes from Part 1

# Check you're in the right directory
pwd  # Should end in /Ops-AI-Student-View
```

### Step 1: Test Docker Build Locally

```bash
docker build -f week2/starter/Dockerfile -t demand-api:test .
docker run --rm -p 8000:8000 demand-api:test

# In another terminal, test:
curl http://localhost:8000/health
# Should return: {"status": "ok"}

# Ctrl+C to stop
```

### Step 2: Deploy to Kubernetes

```bash
# Apply ConfigMap
kubectl apply -f week2/starter/k8s/configmap.yaml

# Apply Deployment
kubectl apply -f week2/starter/k8s/deployment.yaml

# Apply Service
kubectl apply -f week2/starter/k8s/service.yaml

# Verify pods started:
kubectl get pods
# Should show 1-2 pods in Running state
```

### Step 3: Wait for LoadBalancer External IP

```bash
kubectl get svc demand-api -w
# Press Ctrl+C when you see an external IP (may take 30-60 seconds)
```

### Step 4: Test the API

```bash
EXTERNAL_IP=$(kubectl get svc demand-api -o jsonpath='{.status.loadBalancer.ingress[0].ip}')

# Health check
curl http://$EXTERNAL_IP/health

# Heatmap: zone demand by hour & day-of-week
# hour: 0-23, dow: 0-6, date: YYYY-MM-DD, holiday: optional (defaults: "regular")
curl "http://$EXTERNAL_IP/api/heatmap?hour=17&dow=4&date=2026-01-15&holiday=regular"

# Forecast: demand forecast N steps ahead (1 step = 15 min)
# zone_id: Must use a zone with training data (only 57 of 263 zones available: 4,13,24,41,43,45,48,50,68,70,...)
# hour: 0-23, dow: 0-6, steps: 1-96, date: YYYY-MM-DD
curl "http://$EXTERNAL_IP/api/forecast?zone_id=68&hour=17&dow=4&steps=16&date=2026-01-15"

# Recommendations: suggest best zones to pick up riders
# zone_id: Must be from training zones (4,13,24,41,43,45,48,50,68,70,...)
# hour: 0-23, dow: 0-6, n: 1-10 (defaults: 3), holiday: optional (defaults: "regular")
curl "http://$EXTERNAL_IP/api/recommendations?zone_id=68&hour=17&dow=4&date=2026-01-15&n=3&holiday=regular"
```

If requests fail:

```bash
kubectl logs deployment/demand-api --tail=50  # Check pod logs
kubectl describe pod $(kubectl get pods -o jsonpath='{.items[0].metadata.name}')  # Check pod status
```

---

# PART 4: GitHub Actions CI/CD

### Step 1: Add Service Account Key to GitHub

1. Go to your GitHub repo → **Settings** → **Secrets and variables** → **Actions**
2. Click **New repository secret**
3. Name: `GCP_SA_KEY`
4. Value: Paste entire contents of `key.json` (verify it starts with `{` and ends with `}`)
   ```bash
   cat key.json  # Copy the entire output
   ```
5. Save
6. Verify it was saved (you should see `GCP_SA_KEY` listed, though you can't see the value)

### Step 2: Push to GitHub

```bash
git add .
git commit -m "Week 2: Configure deployment (Part 0-4)"
git push origin main
```

**GitHub Actions will:**

1. Run CI (tests + build Docker image)
2. Push image to Artifact Registry
3. Deploy to GKE
4. Check pod health

Watch in GitHub repo → **Actions** tab.

---

# PART 5: Cleanup (CRITICAL - Do This!)

**GKE clusters cost ~$0.10-0.15/hour. Forgetting to delete costs money.**

After you submit:

```bash
# Delete cluster
gcloud container clusters delete operationalizing-ai \
  --zone us-central1-a \
  --project ops-ai-[YOUR-NAME]

# Verify deletion
gcloud container clusters list --project ops-ai-[YOUR-NAME]  # Should be empty

# Delete Artifact Registry (optional, but recommended)
gcloud artifacts repositories delete docker-repo \
  --location=us-central1 \
  --project ops-ai-[YOUR-NAME]

# Keep GCS bucket (needed for Week 3); don't delete
gsutil ls gs://ops-ai-[YOUR-NAME]-data/
```

**Take a screenshot of the deletion confirmation and include in submission.**

Estimated total cost: **$2-5** if cleaned up properly.

---

# Deliverables

Submit on Canvas:

1. **GitHub repo link** (make it private; add instructor as collaborator)
2. **Architecture diagram** (PDF/image)

   - Show: GitHub → Artifact Registry → GKE → external IP
3. **Screenshots PDF** (combine all screenshots into one PDF):

   - API health check: `curl http://$EXTERNAL_IP/health` returning `{"status":"ok"}`
   - kubectl showing pods running and external IP assigned
   - At least one working API endpoint (`/api/heatmap`, `/api/forecast`, or `/api/recommendations`)
   - GKE cluster deletion confirmation

---

# Requirements

### API Functional Requirements

- Load model and data on startup from GCS
- `/health` endpoint returns 200 if ready, 500 if not
- `/api/heatmap`, `/api/forecast`, `/api/recommendations` endpoints work
- Handle 10+ concurrent requests without errors

### Deployment Requirements

- Docker image builds locally and runs correctly
- Image pushed to Artifact Registry with versioned tags
- Kubernetes manifests are correct
- API accessible via LoadBalancer external IP

### CI/CD Requirements

- GitHub Actions CI runs tests and builds on push
- CD deploys only on main branch
- Secrets handled securely (no credentials in code/logs)

---

# Grading

| Criterion                   | Weight |
| --------------------------- | ------ |
| API live & responding       | 35%    |
| CI/CD functional            | 30%    |
| Kubernetes setup correct    | 25%    |
| Cluster properly cleaned up | 10%    |

---

# Troubleshooting

## Docker Build Errors

**Error: "pip install failed" or "No module named X"**

- Solution: Check `week2/backend/requirements.txt` includes all imports from data.py
- Required: `requests`, `lightgbm` (others are included automatically)

## Kubernetes Deployment Errors

**ImagePullBackOff or "image pull failed"**

- Cause: Missing artifact registry secret
- Solution: Run `kubectl create secret docker-registry artifact-registry-secret` (Part 2 Step 1)
- Verify: `kubectl get secrets` should list it

**Pod crashes with "ModuleNotFoundError"**

- Cause: Missing dependencies in requirements.txt
- Solution: Add missing packages and rebuild Docker image

**Pod stuck in "PodInitializing"**

- Cause: Init container downloading data from GCS is slow/failing
- Solution: Check `kubectl logs <pod-name> -c download-data` for GCS errors
- Verify ConfigMap GCS_BUCKET matches actual bucket name
- If you see "Permission denied" errors:
  - Make GCS bucket public: `gsutil iam ch allUsers:objectViewer gs://ops-ai-[YOUR-NAME]-data`
  - OR configure Workload Identity to bind the default K8s service account to your google-actions service account
  - OR mount the github-actions service account key in the init container (see PART 2 Step 1)

**Pod runs but health check fails**

- Cause: Model or data files not loaded correctly
- Solution: Check pod logs: `kubectl logs <pod-name>`
- Verify files in GCS: `gsutil ls gs://ops-ai-[YOUR-NAME]-data/`

**Forecast endpoint returns empty `[]`**

- Cause: zone_id doesn't have training data (only 57 of 263 zones available)
- Solution: Use a valid zone from training data: `4,13,24,41,43,45,48,50,68,70,74,75,79,87,88,90,93,100,107,113,...`
- To find all valid zones: check `week2/metadata/Lookups/taxi_zone_lookup.csv` for zone names, then filter by zones that appear in `week2/data/demand_enriched.parquet`
- Example that works: `/api/forecast?zone_id=68&hour=17&dow=4&steps=16&date=2026-01-15`

## Git & GitHub Errors

**Key.json accidentally committed:**

```bash
# Remove from tracking
git rm --cached key.json
echo "key.json" >> .gitignore
git commit -m "Remove key.json from tracking"
```

**Cluster never gets external IP:**

- Check: `kubectl get svc demand-api` shows service type as LoadBalancer
- Check: At least one pod is Running: `kubectl get pods`
- Wait: GCP may take 60+ seconds to assign external IP

**CI/CD fails with auth errors:**

- Check GitHub secret `GCP_SA_KEY` is pasted completely (not truncated)
- Verify service account has necessary roles (Part 0 Step 7)

---

# Due

End of Week 2 (see syllabus)
