#!/usr/bin/env bash
# Provision PostgreSQL 16 + PostGIS + pgRouting inside WSL for local development.
#
#   wsl -d Ubuntu -u root -- bash /mnt/c/.../scripts/wsl-setup-db.sh
#
# Idempotent: safe to re-run. Development credentials only - this database
# listens on localhost and the loopback-equivalent WSL subnets, never publicly.
set -euo pipefail

PGVER=16
CONF="/etc/postgresql/${PGVER}/main/postgresql.conf"
HBA="/etc/postgresql/${PGVER}/main/pg_hba.conf"
DB=nzcl
ROLE=nzcl
PASS="${NZCL_DB_PASSWORD:-nzcl_local_dev}"

echo "== installing packages =="
export DEBIAN_FRONTEND=noninteractive
dpkg --configure -a >/dev/null 2>&1 || true
apt-get update -qq
apt-get install -y -qq \
  "postgresql-${PGVER}" \
  "postgresql-${PGVER}-postgis-3" \
  "postgresql-${PGVER}-postgis-3-scripts" \
  "postgresql-${PGVER}-pgrouting" \
  python3-venv python3-pip gdal-bin >/dev/null

echo "== configuring =="
if grep -qE "^#?listen_addresses" "$CONF"; then
  sed -i "s|^#\?listen_addresses.*|listen_addresses = '*'|" "$CONF"
else
  echo "listen_addresses = '*'" >> "$CONF"
fi
sed -i "s|^#\?shared_buffers.*|shared_buffers = 2GB|" "$CONF"
sed -i "s|^#\?work_mem.*|work_mem = 128MB|" "$CONF"
sed -i "s|^#\?maintenance_work_mem.*|maintenance_work_mem = 1GB|" "$CONF"
sed -i "s|^#\?max_parallel_workers_per_gather.*|max_parallel_workers_per_gather = 4|" "$CONF"

if ! grep -q "nzcl_dev_rule" "$HBA"; then
  cat >> "$HBA" <<'EOF'

# nzcl_dev_rule - local development access for the nzcl role.
# Loopback plus the private ranges WSL2 uses for the Windows host. Not public.
host    nzcl    nzcl    127.0.0.1/32    scram-sha-256
host    nzcl    nzcl    ::1/128         scram-sha-256
host    nzcl    nzcl    10.0.0.0/8      scram-sha-256
host    nzcl    nzcl    172.16.0.0/12   scram-sha-256
host    nzcl    nzcl    192.168.0.0/16  scram-sha-256
EOF
fi

echo "== starting =="
pg_ctlcluster "$PGVER" main start 2>/dev/null || pg_ctlcluster "$PGVER" main restart
sleep 3

echo "== role and database =="
if ! su - postgres -c "psql -tAc \"SELECT 1 FROM pg_roles WHERE rolname='${ROLE}'\"" | grep -q 1; then
  su - postgres -c "psql -c \"CREATE ROLE ${ROLE} LOGIN PASSWORD '${PASS}' SUPERUSER;\""
else
  su - postgres -c "psql -c \"ALTER ROLE ${ROLE} PASSWORD '${PASS}';\""
fi
if ! su - postgres -c "psql -tAc \"SELECT 1 FROM pg_database WHERE datname='${DB}'\"" | grep -q 1; then
  su - postgres -c "createdb -O ${ROLE} ${DB}"
fi
su - postgres -c "psql -d ${DB} -q -c 'CREATE EXTENSION IF NOT EXISTS postgis; CREATE EXTENSION IF NOT EXISTS pgrouting;'"

echo "== verifying =="
su - postgres -c "psql -d ${DB} -tAc \"SELECT extname || ' ' || extversion FROM pg_extension ORDER BY extname\""
PGPASSWORD="$PASS" psql -h 127.0.0.1 -U "$ROLE" -d "$DB" -tAc "SELECT 'tcp connect ok as ' || current_user"

echo "== done =="
