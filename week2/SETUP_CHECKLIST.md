# Week 2 Setup Checklist

Do these in order.

## Pre-Deployment (Local)

- [ ] Clone repo and verify files:

  ```bash
  ls week2/metadata/Lookups/taxi_zone_lookup.csv
  ls week2/data/demand_enriched.parquet
  ls week2/model/lgbm_demand_model.txt
  ```
- [ ] Test Docker build:

  ```bash
  docker build -f week2/starter/Dockerfile -t demand-api:test .
  docker run -p 8000:8000 demand-api:test &
  sleep 3
  curl http://localhost:8000/health
  pkill -f "docker run"
  ```
  - If the above doesn't work, try the following command by adding volume mounts:
  For MacOS / Linux:
  ```bash
  docker run --rm -p 8000:8000 \
      -v "$(pwd)/week2/data/demand_enriched.parquet:/data/processed/demand_enriched.parquet:ro" \
      -v "$(pwd)/week2/model/lgbm_demand_model.txt:/data/processed/lgbm_demand_model.txt:ro" \
      -v "$(pwd)/week2/backend/zone_hour_avg_fare.parquet:/data/processed/zone_hour_avg_fare.parquet:ro" \
      -v "$(pwd)/week2/backend/taxi_zones.geojson:/app/frontend/public/taxi_zones.geojson:ro" \
      demand-api:test
  ```
  
  For Windows:
  ```bash
  docker run --rm -p 8000:8000 `
      -v "${PWD}\week2\data\demand_enriched.parquet:/data/processed/demand_enriched.parquet:ro" `
      -v "${PWD}\week2\model\lgbm_demand_model.txt:/data/processed/lgbm_demand_model.txt:ro" `
      -v "${PWD}\week2\backend\zone_hour_avg_fare.parquet:/data/processed/zone_hour_avg_fare.parquet:ro" `
      -v "${PWD}\week2\backend\taxi_zones.geojson:/app/frontend/public/taxi_zones.geojson:ro" `
      demand-api:test
  ```


## GCP Setup

- [ ] Create project:

  ```bash
  gcloud projects create ops-ai-[YOUR-NAME] --set-as-default
  gcloud auth login
  ```
- [ ] Set up billing:

  ```bash
  gcloud billing accounts list
  gcloud beta billing projects link ops-ai-[YOUR-NAME] --billing-account=[ID]
  ```
- [ ] Enable APIs:

  ```bash
  gcloud services enable container.googleapis.com
  gcloud services enable artifactregistry.googleapis.com
  gcloud services enable compute.googleapis.com
  gcloud services enable storage-api.googleapis.com
  ```
- [ ] Create GCS bucket:

  ```bashe
  gsutil mb gs://ops-ai-[YOUR-NAME]-data
  gsutil cp week2/data/demand_enriched.parquet gs://ops-ai-[YOUR-NAME]-data/
  gsutil cp week2/model/lgbm_demand_model.txt gs://ops-ai-[YOUR-NAME]-data/
  gsutil cp week2/backend/zone_hour_avg_fare.parquet gs://ops-ai-[YOUR-NAME]-data/
  gsutil cp week2/backend/taxi_zones.geojson gs://ops-ai-[YOUR-NAME]-data/
  ```
- [ ] Create Artifact Registry:

  ```bash
  gcloud artifacts repositories create docker-repo \
    --repository-format=docker \
    --location=us-central1
  ```
- [ ] Create service account and permissions:

  ```bash
  gcloud iam service-accounts create github-actions

  gcloud projects add-iam-policy-binding ops-ai-[YOUR-NAME] \
    --member=serviceAccount:github-actions@ops-ai-[YOUR-NAME].iam.gserviceaccount.com \
    --role=roles/container.developer

  gcloud projects add-iam-policy-binding ops-ai-[YOUR-NAME] \
    --member=serviceAccount:github-actions@ops-ai-[YOUR-NAME].iam.gserviceaccount.com \
    --role=roles/artifactregistry.writer

  gcloud projects add-iam-policy-binding ops-ai-[YOUR-NAME] \
    --member=serviceAccount:github-actions@ops-ai-[YOUR-NAME].iam.gserviceaccount.com \
    --role=roles/artifactregistry.reader

  gcloud projects add-iam-policy-binding ops-ai-[YOUR-NAME] \
    --member=serviceAccount:github-actions@ops-ai-[YOUR-NAME].iam.gserviceaccount.com \
    --role=roles/storage.objectViewer
  ```
- [ ] Create and download service account key:

  ```bash
  gcloud iam service-accounts keys create key.json \
    --iam-account=github-actions@ops-ai-[YOUR-NAME].iam.gserviceaccount.com

  echo "key.json" >> .gitignore
  git add .gitignore
  git commit -m "Add key.json to .gitignore"
  ```

## Kubernetes Cluster

- [ ] Create GKE cluster:

  ```bash
  gcloud container clusters create operationalizing-ai \
    --zone us-central1-a \
    --num-nodes 2 \
    --machine-type n1-standard-2 \
    --enable-autoscaling \
    --min-nodes 2 \
    --max-nodes 5
  ```
- [ ] Get credentials:

  ```bash
  gcloud container clusters get-credentials operationalizing-ai \
    --zone us-central1-a

  kubectl get nodes  # Verify: should show 2 nodes
  ```

## Configuration Files (Edit These)

- [ ] `week2/starter/k8s/configmap.yaml`

  - Replace `[YOUR-NAME]` with your project name
- [ ] `week2/starter/.github/workflows/cd.yml`

  - Replace all `[YOUR-NAME]` with your project name
  - Check image region matches: `us-central1-docker.pkg.dev`
- [ ] `week2/starter/k8s/deployment.yaml`

  - Replace `[YOUR-NAME]` in image path: `us-central1-docker.pkg.dev/[YOUR-NAME]/docker-repo/demand-api:latest`
  - Fill in TODO values:
    - `replicas` (recommend: 2-3 for 10+ concurrent requests)
    - `imagePullPolicy` (use: Always or IfNotPresent)
    - `maxSurge` and `maxUnavailable` (use: 1 each)
    - `cpu/memory requests` (recommend: `512m`/`1Gi`)
    - `cpu/memory limits` (recommend: `1000m`/`3Gi`)
    - Probe timings: `initialDelaySeconds`, `periodSeconds`, `failureThreshold`
- [ ] `week2/starter/k8s/service.yaml`

  - Fill in TODO values:
    - `type`: LoadBalancer (exposes external IP)
    - `selector.app`: demand-api
    - `port`: 80
    - `targetPort`: 8000

## Deploy to Kubernetes

- [ ] Create artifact registry secret:

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
- [ ] Apply Kubernetes manifests:

  ```bash
  kubectl apply -f week2/starter/k8s/configmap.yaml
  kubectl apply -f week2/starter/k8s/deployment.yaml
  kubectl apply -f week2/starter/k8s/service.yaml
  ```
- [ ] Wait for external IP and test:

  ```bash
  kubectl get svc demand-api -w
  # Press Ctrl+C when you see an external IP

  EXTERNAL_IP=$(kubectl get svc demand-api -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
  curl http://$EXTERNAL_IP/health
  curl "http://$EXTERNAL_IP/api/heatmap?hour=12&dow=2&date=2026-01-15"
  ```

## GitHub Actions CI/CD

- [ ] Add service account key to GitHub:

  - Go to repo → Settings → Secrets and variables → Actions
  - New secret: `GCP_SA_KEY` = contents of `key.json`
- [ ] Push to GitHub:

  ```bash
  git add .
  git commit -m "Week 2: Complete setup"
  git push origin main
  ```
- [ ] Watch GitHub Actions → verify build and deploy succeed

## Cleanup (AFTER SUBMISSION)

- [ ] Delete cluster:

  ```bash
  gcloud container clusters delete operationalizing-ai \
    --zone us-central1-a
  ```
- [ ] Verify deletion:

  ```bash
  gcloud container clusters list  # Should be empty
  ```
- [ ] Delete Artifact Registry (optional):

  ```bash
  gcloud artifacts repositories delete docker-repo \
    --location=us-central1
  ```
- [ ] Keep GCS bucket (needed for Week 3)
