# Pipe1 infrastructure (Terraform)

GCP foundation for the web app: APIs, VPC with private services access, Cloud SQL for PostgreSQL 17 (private IP), media bucket, KMS key for TOTP secrets, Secret Manager secrets, Artifact Registry, and least-privilege service accounts for the API and worker. Design: `docs/Web_Architecture.md` §3.

Cloud Run services, Cloud Tasks queues, the HTTPS load balancer, Cloud Armor and IAP are added once container images exist.

## One-time bootstrap: state bucket

```bash
gcloud storage buckets create gs://project-852c811d-dbd5-4e19-957-tfstate \
  --project=project-852c811d-dbd5-4e19-957 --location=asia-northeast3 \
  --uniform-bucket-level-access --public-access-prevention
gcloud storage buckets update gs://project-852c811d-dbd5-4e19-957-tfstate --versioning
```

The state contains the generated database passwords, so access to this bucket must stay limited to people who administer the project.

## Usage

```bash
cd infra/terraform
terraform init -backend-config=envs/dev.backend.hcl
terraform plan -var-file=envs/dev.tfvars
terraform apply -var-file=envs/dev.tfvars    # creates billed resources
```

After apply, add the SendGrid API key without putting it in Terraform state:

```bash
gcloud secrets versions add pipe1-dev-sendgrid-api-key --data-file=-   # paste key, then Ctrl-D
```

## Cost notes (dev)

The main recurring cost is the Cloud SQL instance (`db-f1-micro`, zonal). Everything else is near zero at dev volumes. Set `deletion_protection = false` and run `terraform destroy` to remove it when not needed.
