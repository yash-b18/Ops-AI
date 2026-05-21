# Week 2 Requirements & Setup

## System Requirements

**All Platforms:**
- Python 3.11+
- Docker Desktop (or Docker Engine on Linux)
- Git
- kubectl (Kubernetes CLI)
- gcloud CLI
- ~10GB free disk space

### Installing gcloud CLI

**macOS:**
```bash
brew install --cask google-cloud-sdk
gcloud init
gcloud auth login
```

**Windows (WSL2):**
```bash
curl https://sdk.cloud.google.com | bash
exec -l $SHELL
gcloud init
gcloud auth login
```

Or use the [official installer](https://cloud.google.com/sdk/docs/install-sdk#windows).

**macOS/Linux:**
- No special setup needed beyond above.

**Windows:**
- Use WSL2 (Windows Subsystem for Linux 2)
- Docker Desktop with WSL2 backend
- Set: `git config --global core.autocrlf input`

## GCP Setup (Your Responsibility)

You create and manage your own GCP project:

```bash
# Create project
gcloud projects create ops-ai-[YOUR-NAME] --set-as-default

# Login
gcloud auth login
```

See **Week 2 README Part 0** for full GCP setup instructions.

## Data Files

Pre-loaded in this repo:
- `week2/data/demand_enriched.parquet` — training data
- `week2/model/lgbm_demand_model.txt` — pre-trained LightGBM model

You upload these to your GCS bucket during Part 0 setup.

## Python Requirements

**Packages** (in `week2/backend/requirements.txt`):
- fastapi, uvicorn, pandas, pyarrow, numpy, lightgbm, scikit-learn

**Important for Dockerfile:**
- LightGBM requires OpenMP library (`libgomp1`)
- Dockerfile installs this automatically (Linux)
- macOS users: `brew install libgomp`

## Docker & Kubernetes

### Installing Docker

**macOS:**
```bash
brew install --cask docker
# Then open Docker.app from Applications to start the daemon
```

**Windows (WSL2):**
```bash
# Download and install Docker Desktop with WSL2 backend from:
# https://www.docker.com/products/docker-desktop
```

**Verify:**
```bash
docker --version  # Should be 20.10+
docker run hello-world  # Quick test
```

### Installing kubectl

**macOS:**
```bash
brew install kubectl
```

**Windows (WSL2):**
```bash
# From within WSL2:
curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"
chmod +x kubectl
sudo mv kubectl /usr/local/bin/
```

**Verify:**
```bash
kubectl version --client  # Should show a version
```

**GKE cluster:** You'll create this in Week 2 Part 1 (costs ~$0.10-0.15/hour while running)

## Checklist

Before starting Week 2, verify:
- [ ] Python 3.11+ installed: `python --version`
- [ ] Docker installed: `docker --version`
- [ ] Git configured: `git config --global user.email`
- [ ] kubectl installed: `kubectl version --client`
- [ ] gcloud installed and authenticated: `gcloud config list`

## Known Issues & Workarounds

**Git LFS doesn't work:**
→ Run `git lfs pull` manually; if that fails, data files are small enough to download directly

**libgomp error in Docker:**
→ Dockerfile includes `RUN apt-get install -y libgomp1`, ensure it's there

**requests module not found:**
→ Check `week2/backend/requirements.txt` has it (it should)

**Pod can't access GCS:**
→ Service account doesn't have correct permissions; see Week 2 Part 0 GCP setup

**Windows line endings break Docker:**
→ Set `git config --global core.autocrlf input`

**Docker build is slow on Windows:**
→ Use WSL2 backend, not Hyper-V. Consider developing in WSL2 directly.

**GKE cluster creation times out:**
→ May take 5-10 minutes. Be patient. Check: `gcloud container clusters list --project ops-ai-[YOUR-NAME]`
