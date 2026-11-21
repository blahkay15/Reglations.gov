project     = "regs-docket"
environment = "dev"
aws_region  = "us-east-1"

# Optional. Leave empty and Blake's kit can merge its own tags at apply time.
tags = {}

page_size            = 20
map_max_concurrency  = 2
bedrock_model_id     = "us.amazon.nova-lite-v1:0"
max_labeled_clusters = 40
agent_image_tag      = "latest"
