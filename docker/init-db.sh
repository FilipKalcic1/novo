#!/bin/bash
set -e

# This script runs when PostgreSQL container starts
# It ensures the database exists even if volumes are reused

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "postgres" <<-EOSQL
    -- Create database if not exists
    SELECT 'CREATE DATABASE mobility_db'
    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'mobility_db')\gexec

    -- Grant privileges
    GRANT ALL PRIVILEGES ON DATABASE mobility_db TO $POSTGRES_USER;
EOSQL

echo "Database mobility_db is ready"
