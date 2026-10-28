-- Local development only. Cloud SQL roles are created by Terraform with real passwords.
CREATE ROLE pipe1_migrator LOGIN PASSWORD 'pipe1_migrator';
CREATE ROLE pipe1_app LOGIN PASSWORD 'pipe1_app';
CREATE DATABASE pipe1 OWNER pipe1_migrator;
