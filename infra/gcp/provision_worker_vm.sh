#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/load_python_env.sh"

gcloud() {
  env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u http_proxy -u https_proxy -u all_proxy gcloud "$@"
}

: "${PROJECT_ID:?PROJECT_ID is required}"
: "${REGION:?REGION is required}"
: "${CLOUD_SQL_INSTANCE:?CLOUD_SQL_INSTANCE is required}"
: "${BUCKET_NAME:?BUCKET_NAME is required}"
: "${REDIS_RUNTIME:=memorystore}"
: "${REDIS_VM_PORT:=6379}"
: "${REDIS_VM_FIREWALL_RULE:=capstone-worker-redis-allow}"
: "${REDIS_VM_ALLOWED_SOURCE:=}"
: "${WORKER_VM_NAME:=audio-summarizer-worker-vm}"
: "${WORKER_VM_ZONE:=us-central1-a}"
: "${WORKER_VM_MACHINE_TYPE:=e2-standard-2}"
: "${WORKER_VM_DISK_SIZE_GB:=64}"
: "${WORKER_VM_IMAGE_FAMILY:=debian-12}"
: "${WORKER_VM_IMAGE_PROJECT:=debian-cloud}"
: "${WORKER_VM_NETWORK:=default}"
: "${VPC_CONNECTOR_RANGE:=10.8.0.0/28}"
: "${WORKER_VM_SUBNET:=}"
: "${WORKER_VM_ENABLE_RDP:=true}"
: "${WORKER_VM_RDP_SOURCE:=0.0.0.0/0}"
: "${WORKER_VM_USER:=worker}"
: "${WORKER_VM_SERVICE_ACCOUNT:=audio-summarizer-worker-vm-sa}"

INSTANCE_CONN="${PROJECT_ID}:${REGION}:${CLOUD_SQL_INSTANCE}"
VM_SA_EMAIL="${WORKER_VM_SERVICE_ACCOUNT}@${PROJECT_ID}.iam.gserviceaccount.com"
RDP_TAG="capstone-worker-rdp"
RDP_RULE="capstone-worker-rdp-allow"
STARTUP_SCRIPT="$(mktemp)"

cleanup() {
  rm -f "${STARTUP_SCRIPT}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

gcloud config set project "${PROJECT_ID}" >/dev/null

gcloud services enable \
  compute.googleapis.com \
  iam.googleapis.com \
  sqladmin.googleapis.com \
  redis.googleapis.com \
  storage.googleapis.com >/dev/null

gcloud iam service-accounts create "${WORKER_VM_SERVICE_ACCOUNT}" \
  --display-name="Audio Summarizer Worker VM runtime" \
  >/dev/null 2>&1 || true

for role in roles/cloudsql.client roles/logging.logWriter roles/monitoring.metricWriter; do
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${VM_SA_EMAIL}" \
    --role="${role}" \
    >/dev/null
done

gcloud storage buckets add-iam-policy-binding "gs://${BUCKET_NAME}" \
  --member="serviceAccount:${VM_SA_EMAIL}" \
  --role="roles/storage.objectAdmin" \
  >/dev/null

if [[ "${WORKER_VM_ENABLE_RDP,,}" == "true" ]]; then
  if ! gcloud compute firewall-rules describe "${RDP_RULE}" >/dev/null 2>&1; then
    gcloud compute firewall-rules create "${RDP_RULE}" \
      --direction=INGRESS \
      --network="${WORKER_VM_NETWORK}" \
      --action=ALLOW \
      --rules=tcp:3389 \
      --source-ranges="${WORKER_VM_RDP_SOURCE}" \
      --target-tags="${RDP_TAG}" \
      >/dev/null
  else
    gcloud compute firewall-rules update "${RDP_RULE}" \
      --source-ranges="${WORKER_VM_RDP_SOURCE}" \
      --target-tags="${RDP_TAG}" \
      >/dev/null
  fi
fi

if [[ "${REDIS_RUNTIME}" == "worker_vm" ]]; then
  if [[ -z "${REDIS_VM_ALLOWED_SOURCE}" ]]; then
    REDIS_VM_ALLOWED_SOURCE="${VPC_CONNECTOR_RANGE}"
  fi
  if ! gcloud compute firewall-rules describe "${REDIS_VM_FIREWALL_RULE}" >/dev/null 2>&1; then
    gcloud compute firewall-rules create "${REDIS_VM_FIREWALL_RULE}" \
      --direction=INGRESS \
      --network="${WORKER_VM_NETWORK}" \
      --action=ALLOW \
      --rules="tcp:${REDIS_VM_PORT}" \
      --source-ranges="${REDIS_VM_ALLOWED_SOURCE}" \
      --target-tags=capstone-worker-vm \
      >/dev/null
  else
    gcloud compute firewall-rules update "${REDIS_VM_FIREWALL_RULE}" \
      --source-ranges="${REDIS_VM_ALLOWED_SOURCE}" \
      --target-tags=capstone-worker-vm \
      >/dev/null
  fi
fi

cat > "${STARTUP_SCRIPT}" <<EOF
#!/usr/bin/env bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y --no-install-recommends \
  ca-certificates curl gnupg lsb-release wget \
  python3 python3-venv python3-pip \
  ffmpeg git jq \
  redis-server \
  xrdp xfce4 xfce4-goodies dbus-x11 xorgxrdp

if ! command -v google-chrome >/dev/null 2>&1; then
  wget -q -O /tmp/google-chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
  apt-get install -y /tmp/google-chrome.deb || (apt-get -f install -y && apt-get install -y /tmp/google-chrome.deb)
fi

# yt-dlp JS challenge solving requires a supported JavaScript runtime.
# Debian default node can be too old; install Node 22 from NodeSource.
if ! command -v node >/dev/null 2>&1 || ! node --version | grep -Eq '^v2[2-9]\.'; then
  curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
  apt-get install -y nodejs
fi

if ! command -v cloud-sql-proxy >/dev/null 2>&1; then
  curl -fL "https://storage.googleapis.com/cloud-sql-connectors/cloud-sql-proxy/v2.21.1/cloud-sql-proxy.linux.amd64" \
    -o /usr/local/bin/cloud-sql-proxy
  chmod +x /usr/local/bin/cloud-sql-proxy
fi

if ! id -u ${WORKER_VM_USER} >/dev/null 2>&1; then
  useradd -m -s /bin/bash ${WORKER_VM_USER}
fi

mkdir -p /opt/capstone /etc/capstone /cloudsql
chown -R ${WORKER_VM_USER}:${WORKER_VM_USER} /opt/capstone /cloudsql /home/${WORKER_VM_USER}

touch /etc/redis/redis-capstone.conf
if ! grep -Fq 'include /etc/redis/redis-capstone.conf' /etc/redis/redis.conf; then
  echo 'include /etc/redis/redis-capstone.conf' >> /etc/redis/redis.conf
fi

echo "startxfce4" > /home/${WORKER_VM_USER}/.xsession
chown ${WORKER_VM_USER}:${WORKER_VM_USER} /home/${WORKER_VM_USER}/.xsession
chmod 644 /home/${WORKER_VM_USER}/.xsession

cat > /etc/systemd/system/cloud-sql-proxy.service <<'SYSTEMD'
[Unit]
Description=Cloud SQL Auth Proxy
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${WORKER_VM_USER}
Group=${WORKER_VM_USER}
ExecStart=/usr/local/bin/cloud-sql-proxy ${INSTANCE_CONN} --unix-socket=/cloudsql --structured-logs
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SYSTEMD

cat > /etc/systemd/system/capstone-worker.service <<'SYSTEMD'
[Unit]
Description=Capstone RQ Worker
After=network-online.target cloud-sql-proxy.service
Wants=network-online.target
Requires=cloud-sql-proxy.service

[Service]
Type=simple
User=${WORKER_VM_USER}
Group=${WORKER_VM_USER}
WorkingDirectory=/opt/capstone
EnvironmentFile=/etc/capstone/worker.env
ExecStart=/opt/capstone/.venv/bin/python -m backend.webapp.worker
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SYSTEMD

systemctl daemon-reload
systemctl enable xrdp || true
systemctl restart xrdp || true
systemctl enable redis-server || true
systemctl restart redis-server || true
systemctl enable cloud-sql-proxy capstone-worker
systemctl restart cloud-sql-proxy || true
systemctl restart capstone-worker || true
EOF

if gcloud compute instances describe "${WORKER_VM_NAME}" --zone "${WORKER_VM_ZONE}" >/dev/null 2>&1; then
  echo "Worker VM '${WORKER_VM_NAME}' already exists; updating startup script metadata."
  gcloud compute instances add-metadata "${WORKER_VM_NAME}" \
    --zone "${WORKER_VM_ZONE}" \
    --metadata-from-file startup-script="${STARTUP_SCRIPT}" \
    >/dev/null
  echo "Restarting VM to apply startup script updates..."
  gcloud compute instances reset "${WORKER_VM_NAME}" --zone "${WORKER_VM_ZONE}" >/dev/null
else
  INSTANCE_TAGS="capstone-worker-vm"
  if [[ "${WORKER_VM_ENABLE_RDP,,}" == "true" ]]; then
    INSTANCE_TAGS="${INSTANCE_TAGS},${RDP_TAG}"
  fi

  CREATE_ARGS=(
    compute instances create "${WORKER_VM_NAME}"
    --zone "${WORKER_VM_ZONE}"
    --machine-type "${WORKER_VM_MACHINE_TYPE}"
    --boot-disk-size "${WORKER_VM_DISK_SIZE_GB}GB"
    --image-family "${WORKER_VM_IMAGE_FAMILY}"
    --image-project "${WORKER_VM_IMAGE_PROJECT}"
    --service-account "${VM_SA_EMAIL}"
    --scopes cloud-platform
    --tags "${INSTANCE_TAGS}"
    --metadata-from-file startup-script="${STARTUP_SCRIPT}"
  )

  if [[ -n "${WORKER_VM_SUBNET}" ]]; then
    CREATE_ARGS+=(--subnet "${WORKER_VM_SUBNET}")
  else
    CREATE_ARGS+=(--network "${WORKER_VM_NETWORK}")
  fi

  gcloud "${CREATE_ARGS[@]}" >/dev/null
fi

VM_IP="$(gcloud compute instances describe "${WORKER_VM_NAME}" --zone "${WORKER_VM_ZONE}" --format='value(networkInterfaces[0].accessConfigs[0].natIP)')"
echo "Worker VM ready: ${WORKER_VM_NAME} (${WORKER_VM_ZONE})"
echo "External IP: ${VM_IP}"
echo "Next steps:"
echo "  1) Set Linux password for '${WORKER_VM_USER}':"
echo "     gcloud compute ssh ${WORKER_VM_NAME} --zone ${WORKER_VM_ZONE} --command \"sudo passwd ${WORKER_VM_USER}\""
if [[ "${WORKER_VM_ENABLE_RDP,,}" == "true" ]]; then
  echo "  2) Connect via RDP to ${VM_IP}:3389 as user '${WORKER_VM_USER}'"
  echo "  3) Open Chrome on the VM desktop and sign into YouTube once"
fi
