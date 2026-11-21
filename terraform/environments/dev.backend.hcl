# Copy is not required. Pass this file to terraform init.
# Replace the bucket and lock table with the names bootstrap created.
bucket         = "REPLACE_WITH_STATE_BUCKET"
key            = "regs-docket/dev/terraform.tfstate"
region         = "us-east-1"
dynamodb_table = "REPLACE_WITH_LOCK_TABLE"
encrypt        = true
